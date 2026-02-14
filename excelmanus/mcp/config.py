"""MCP 配置管理模块。

负责 MCP Server 连接配置的定义、加载和校验。
支持从 JSON 配置文件（mcp.json）解析 MCP Server 列表。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger("excelmanus.mcp.config")

# 环境变量名：指定 MCP 配置文件路径
_ENV_MCP_CONFIG = "EXCELMANUS_MCP_CONFIG"

# 合法的传输方式
_VALID_TRANSPORTS = {"stdio", "sse"}


@dataclass
class MCPServerConfig:
    """单个 MCP Server 的连接配置。"""

    name: str                                   # 服务器名称，用于工具前缀
    transport: Literal["stdio", "sse"]          # 传输方式
    command: str | None = None                  # stdio: 启动命令
    args: list[str] = field(default_factory=list)  # stdio: 命令参数
    env: dict[str, str] = field(default_factory=dict)  # stdio: 环境变量
    url: str | None = None                      # sse: 端点 URL
    timeout: int = 30                           # 工具调用超时（秒）


class MCPConfigLoader:
    """MCP 配置文件加载器。"""

    @staticmethod
    def load(
        config_path: str | None = None,
        workspace_root: str = ".",
    ) -> list[MCPServerConfig]:
        """加载 MCP Server 配置列表。

        搜索顺序：
        1. EXCELMANUS_MCP_CONFIG 环境变量指定的路径
        2. config_path 参数
        3. {workspace_root}/mcp.json
        4. ~/.excelmanus/mcp.json

        返回空列表表示无配置或配置文件不存在。
        """
        # 按优先级构建候选路径列表
        candidates: list[Path] = []

        env_path = os.environ.get(_ENV_MCP_CONFIG)
        if env_path:
            candidates.append(Path(env_path))

        if config_path:
            candidates.append(Path(config_path))

        candidates.append(Path(workspace_root) / "mcp.json")
        candidates.append(Path.home() / ".excelmanus" / "mcp.json")

        # 逐个尝试，找到第一个存在的文件
        for path in candidates:
            resolved = path.expanduser().resolve()
            if resolved.is_file():
                logger.debug("使用 MCP 配置文件: %s", resolved)
                return MCPConfigLoader._load_file(resolved)

        # 所有候选路径均不存在，静默返回空列表
        logger.debug("未找到 MCP 配置文件，跳过 MCP 初始化")
        return []

    @staticmethod
    def _load_file(path: Path) -> list[MCPServerConfig]:
        """读取并解析指定路径的配置文件。"""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("读取 MCP 配置文件失败: %s - %s", path, exc)
            return []

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error("MCP 配置文件 JSON 格式错误: %s - %s", path, exc)
            return []

        if not isinstance(data, dict):
            logger.error("MCP 配置文件顶层必须为 JSON 对象: %s", path)
            return []

        return MCPConfigLoader._parse_config(data)

    @staticmethod
    def _parse_config(data: dict) -> list[MCPServerConfig]:
        """解析 JSON 配置为 MCPServerConfig 列表。

        格式不合法的条目记录警告并跳过。
        """
        servers_dict = data.get("mcpServers")
        if not isinstance(servers_dict, dict):
            logger.warning("MCP 配置缺少 'mcpServers' 字段或格式不正确，跳过")
            return []

        configs: list[MCPServerConfig] = []
        for name, entry in servers_dict.items():
            if not isinstance(name, str) or not name.strip():
                logger.warning("MCP Server 名称无效，跳过: %r", name)
                continue
            if not isinstance(entry, dict):
                logger.warning("MCP Server '%s' 配置必须为 JSON 对象，跳过", name)
                continue

            config = MCPConfigLoader._validate_server_config(name, entry)
            if config is not None:
                configs.append(config)

        return configs

    @staticmethod
    def _validate_server_config(
        name: str, entry: dict
    ) -> MCPServerConfig | None:
        """校验单个 Server 配置，返回 None 表示无效。"""
        # 校验 transport 字段
        transport = entry.get("transport")
        if transport not in _VALID_TRANSPORTS:
            logger.warning(
                "MCP Server '%s' 的 transport 必须为 'stdio' 或 'sse'，"
                "当前值: %r，跳过",
                name,
                transport,
            )
            return None

        # stdio 类型校验
        if transport == "stdio":
            command = entry.get("command")
            if not isinstance(command, str) or not command.strip():
                logger.warning(
                    "MCP Server '%s' (stdio) 缺少有效的 command 字段，跳过",
                    name,
                )
                return None

            # 解析可选字段
            args = entry.get("args")
            if args is not None:
                if not isinstance(args, list) or not all(
                    isinstance(a, str) for a in args
                ):
                    logger.warning(
                        "MCP Server '%s' 的 args 必须为字符串列表，跳过",
                        name,
                    )
                    return None
            else:
                args = []

            env = entry.get("env")
            if env is not None:
                if not isinstance(env, dict) or not all(
                    isinstance(k, str) and isinstance(v, str)
                    for k, v in env.items()
                ):
                    logger.warning(
                        "MCP Server '%s' 的 env 必须为字符串字典，跳过",
                        name,
                    )
                    return None
            else:
                env = {}

            timeout = MCPConfigLoader._parse_timeout(name, entry)
            if timeout is None:
                return None

            return MCPServerConfig(
                name=name,
                transport="stdio",
                command=command,
                args=args,
                env=env,
                timeout=timeout,
            )

        # sse 类型校验
        if transport == "sse":
            url = entry.get("url")
            if not isinstance(url, str) or not url.strip():
                logger.warning(
                    "MCP Server '%s' (sse) 缺少有效的 url 字段，跳过",
                    name,
                )
                return None

            timeout = MCPConfigLoader._parse_timeout(name, entry)
            if timeout is None:
                return None

            return MCPServerConfig(
                name=name,
                transport="sse",
                url=url,
                timeout=timeout,
            )

        return None  # pragma: no cover

    @staticmethod
    def _parse_timeout(name: str, entry: dict) -> int | None:
        """解析 timeout 字段，无效时返回 None。"""
        timeout = entry.get("timeout", 30)
        if not isinstance(timeout, (int, float)):
            logger.warning(
                "MCP Server '%s' 的 timeout 必须为数字，当前值: %r，跳过",
                name,
                timeout,
            )
            return None
        timeout = int(timeout)
        if timeout < 1:
            logger.warning(
                "MCP Server '%s' 的 timeout 必须 >= 1，当前值: %d，跳过",
                name,
                timeout,
            )
            return None
        return timeout
