"""MCP 配置管理模块。

负责 MCP Server 连接配置的定义、加载和校验。
支持从 JSON 配置文件（mcp.json）解析 MCP Server 列表。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("excelmanus.mcp.config")

# 环境变量名：指定 MCP 配置文件路径
_ENV_MCP_CONFIG = "EXCELMANUS_MCP_CONFIG"
_ENV_MCP_STRICT_SECRETS = "EXCELMANUS_MCP_STRICT_SECRETS"
_ENV_MCP_EXPAND_ENV_REFS = "EXCELMANUS_MCP_EXPAND_ENV_REFS"
_ENV_MCP_UNDEFINED_ENV = "EXCELMANUS_MCP_UNDEFINED_ENV"
_ENV_MCP_ENABLE_STREAMABLE_HTTP = "EXCELMANUS_MCP_ENABLE_STREAMABLE_HTTP"

# 合法的传输方式
_VALID_TRANSPORTS = {"stdio", "sse", "streamable_http"}

# 疑似敏感字段关键字（统一按小写匹配）
_SENSITIVE_KEYWORDS = (
    "api-key",
    "apikey",
    "api_key",
    "token",
    "secret",
    "password",
    "passwd",
    "access-key",
    "access_key",
    "client-secret",
    "client_secret",
)

# 环境变量引用（$VAR 或 ${VAR}）
_ENV_VAR_REF_PATTERN = re.compile(
    r"^\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})$"
)
# 字符串内环境变量 token（支持内联替换）
_ENV_VAR_TOKEN_PATTERN = re.compile(
    r"\$(\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)"
)

_UndefinedEnvPolicy = Literal["keep", "empty", "error"]


@dataclass
class MCPServerConfig:
    """单个 MCP Server 的连接配置。"""

    name: str  # 服务器名称，用于工具前缀
    transport: Literal["stdio", "sse", "streamable_http"]  # 传输方式
    command: str | None = None  # stdio: 启动命令
    args: list[str] = field(default_factory=list)  # stdio: 命令参数
    env: dict[str, str] = field(default_factory=dict)  # stdio: 环境变量
    url: str | None = None  # sse/streamable_http: 端点 URL
    headers: dict[str, str] = field(default_factory=dict)  # sse/streamable_http: HTTP 头
    state_dir: str | None = None  # 进程识别目录（覆盖默认 <workspace>/.excelmanus/mcp）
    timeout: int = 30  # 工具调用超时（秒）
    auto_approve: list[str] = field(default_factory=list)  # 自动批准的工具名列表（白名单）


class MCPConfigLoader:
    """MCP 配置文件加载器。"""

    @staticmethod
    def load(
        config_path: str | None = None,
        workspace_root: str = ".",
        *,
        expand_env_refs: bool | None = None,
        strict_secret_check: bool | None = None,
        undefined_env: _UndefinedEnvPolicy | None = None,
    ) -> list[MCPServerConfig]:
        """加载 MCP Server 配置列表。

        搜索顺序：
        1. EXCELMANUS_MCP_CONFIG 环境变量指定的路径
        2. config_path 参数
        3. {workspace_root}/mcp.json
        4. ~/.excelmanus/mcp.json

        Args:
            config_path: 显式配置文件路径。
            workspace_root: 工作区根目录。
            expand_env_refs: 是否展开 ``$VAR``/``${VAR}``。None 时走环境变量默认值。
            strict_secret_check: 是否启用敏感信息严格模式（命中即阻断该 server）。
            undefined_env: 未定义环境变量处理策略：
                - ``keep``: 保留原始 token；
                - ``empty``: 替换为空字符串；
                - ``error``: 该 server 判定无效并跳过。

        返回空列表表示无配置或配置文件不存在。
        """
        expand_env_refs_resolved = MCPConfigLoader._resolve_expand_env_refs(
            expand_env_refs
        )
        strict_secret_check_resolved = MCPConfigLoader._resolve_strict_secret_check(
            strict_secret_check
        )
        undefined_env_resolved = MCPConfigLoader._resolve_undefined_env_policy(
            undefined_env
        )
        streamable_http_enabled = MCPConfigLoader._parse_bool_env(
            _ENV_MCP_ENABLE_STREAMABLE_HTTP,
            default=True,
        )

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
                return MCPConfigLoader._load_file(
                    resolved,
                    expand_env_refs=expand_env_refs_resolved,
                    strict_secret_check=strict_secret_check_resolved,
                    undefined_env=undefined_env_resolved,
                    streamable_http_enabled=streamable_http_enabled,
                )

        # 所有候选路径均不存在，静默返回空列表
        logger.debug("未找到 MCP 配置文件，跳过 MCP 初始化")
        return []

    @staticmethod
    def _resolve_expand_env_refs(value: bool | None) -> bool:
        if value is not None:
            return bool(value)
        return MCPConfigLoader._parse_bool_env(_ENV_MCP_EXPAND_ENV_REFS, default=True)

    @staticmethod
    def _resolve_strict_secret_check(value: bool | None) -> bool:
        if value is not None:
            return bool(value)
        return MCPConfigLoader._parse_bool_env(
            _ENV_MCP_STRICT_SECRETS,
            default=False,
        )

    @staticmethod
    def _resolve_undefined_env_policy(
        value: _UndefinedEnvPolicy | None,
    ) -> _UndefinedEnvPolicy:
        if value is not None:
            return value
        raw = (os.environ.get(_ENV_MCP_UNDEFINED_ENV) or "").strip().lower()
        if raw in {"keep", "empty", "error"}:
            return raw  # type: ignore[return-value]
        return "keep"

    @staticmethod
    def _parse_bool_env(name: str, *, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        logger.warning(
            "环境变量 %s 值无效(%r)，回退默认值 %s",
            name,
            raw,
            default,
        )
        return default

    @staticmethod
    def _load_file(
        path: Path,
        *,
        expand_env_refs: bool,
        strict_secret_check: bool,
        undefined_env: _UndefinedEnvPolicy,
        streamable_http_enabled: bool,
    ) -> list[MCPServerConfig]:
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

        return MCPConfigLoader._parse_config(
            data,
            expand_env_refs=expand_env_refs,
            strict_secret_check=strict_secret_check,
            undefined_env=undefined_env,
            streamable_http_enabled=streamable_http_enabled,
        )

    @staticmethod
    def _parse_config(
        data: dict,
        *,
        expand_env_refs: bool = True,
        strict_secret_check: bool = False,
        undefined_env: _UndefinedEnvPolicy = "keep",
        streamable_http_enabled: bool = True,
    ) -> list[MCPServerConfig]:
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

            config = MCPConfigLoader._validate_server_config(
                name,
                entry,
                expand_env_refs=expand_env_refs,
                strict_secret_check=strict_secret_check,
                undefined_env=undefined_env,
                streamable_http_enabled=streamable_http_enabled,
            )
            if config is not None:
                configs.append(config)

        return configs

    @staticmethod
    def _validate_server_config(
        name: str,
        entry: dict,
        *,
        expand_env_refs: bool,
        strict_secret_check: bool,
        undefined_env: _UndefinedEnvPolicy,
        streamable_http_enabled: bool,
    ) -> MCPServerConfig | None:
        """校验单个 Server 配置，返回 None 表示无效。"""
        # 校验 transport 字段
        transport = entry.get("transport")
        if transport not in _VALID_TRANSPORTS:
            logger.warning(
                "MCP Server '%s' 的 transport 必须为 %s，当前值: %r，跳过",
                name,
                sorted(_VALID_TRANSPORTS),
                transport,
            )
            return None
        if transport == "streamable_http" and not streamable_http_enabled:
            logger.warning(
                "MCP Server '%s' 使用 streamable_http，但 EXCELMANUS_MCP_ENABLE_STREAMABLE_HTTP=false，已跳过",
                name,
            )
            return None

        warning_paths = MCPConfigLoader._collect_plaintext_secret_paths(entry)
        for field_path in warning_paths:
            logger.warning(
                "MCP Server '%s' 检测到疑似明文敏感配置：%s。"
                "建议改为环境变量引用（$VAR 或 ${VAR}）。",
                name,
                field_path,
            )
        if strict_secret_check and warning_paths:
            logger.error(
                "MCP Server '%s' 启用严格敏感信息检查，因疑似明文配置已跳过。",
                name,
            )
            return None

        effective_entry = entry
        if expand_env_refs:
            try:
                effective_entry = MCPConfigLoader._expand_entry_env_refs(
                    entry,
                    undefined_env=undefined_env,
                )
            except ValueError as exc:
                logger.warning(
                    "MCP Server '%s' 环境变量展开失败: %s，已跳过",
                    name,
                    exc,
                )
                return None

        timeout = MCPConfigLoader._parse_timeout(name, effective_entry)
        if timeout is None:
            return None
        auto_approve = MCPConfigLoader._parse_auto_approve(name, effective_entry)
        state_dir = MCPConfigLoader._parse_optional_str(
            name,
            effective_entry,
            field="stateDir",
            aliases=("state_dir",),
        )
        if state_dir is ...:
            return None

        # stdio 类型校验
        if transport == "stdio":
            command = effective_entry.get("command")
            if not isinstance(command, str) or not command.strip():
                logger.warning(
                    "MCP Server '%s' (stdio) 缺少有效的 command 字段，跳过",
                    name,
                )
                return None

            # 解析可选字段
            args = effective_entry.get("args")
            if args is not None:
                if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                    logger.warning(
                        "MCP Server '%s' 的 args 必须为字符串列表，跳过",
                        name,
                    )
                    return None
            else:
                args = []

            env = effective_entry.get("env")
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

            return MCPServerConfig(
                name=name,
                transport="stdio",
                command=command,
                args=args,
                env=env,
                timeout=timeout,
                auto_approve=auto_approve,
                state_dir=state_dir,
            )

        url = effective_entry.get("url")
        if not isinstance(url, str) or not url.strip():
            logger.warning(
                "MCP Server '%s' (%s) 缺少有效的 url 字段，跳过",
                name,
                transport,
            )
            return None
        headers = MCPConfigLoader._parse_headers(name, effective_entry)
        if headers is None:
            return None

        if transport == "sse":
            return MCPServerConfig(
                name=name,
                transport="sse",
                url=url,
                headers=headers,
                timeout=timeout,
                auto_approve=auto_approve,
                state_dir=state_dir,
            )

        return MCPServerConfig(
            name=name,
            transport="streamable_http",
            url=url,
            headers=headers,
            timeout=timeout,
            auto_approve=auto_approve,
            state_dir=state_dir,
        )

    @staticmethod
    def _parse_optional_str(
        name: str,
        entry: dict[str, Any],
        *,
        field: str,
        aliases: tuple[str, ...] = (),
    ) -> str | None | Any:
        """读取可选字符串字段。

        返回:
            str | None: 合法值；
            Ellipsis: 非法（调用方应返回 None）。
        """
        raw = entry.get(field)
        if raw is None:
            for alias in aliases:
                raw = entry.get(alias)
                if raw is not None:
                    break
        if raw is None:
            return None
        if not isinstance(raw, str):
            logger.warning("MCP Server '%s' 的 %s 必须为字符串，跳过", name, field)
            return ...
        return raw.strip() or None

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

    @staticmethod
    def _parse_auto_approve(name: str, entry: dict) -> list[str]:
        """解析 autoApprove 字段（白名单工具列表）。

        支持特殊值 ``["*"]`` 表示该 Server 的所有工具自动批准。
        无效格式时记录警告并返回空列表（即全部需要审批）。
        """
        raw = entry.get("autoApprove")
        if raw is None:
            return []
        if not isinstance(raw, list) or not all(isinstance(a, str) for a in raw):
            logger.warning(
                "MCP Server '%s' 的 autoApprove 必须为字符串列表，已忽略",
                name,
            )
            return []
        return list(raw)

    @staticmethod
    def _parse_headers(name: str, entry: dict[str, Any]) -> dict[str, str] | None:
        """解析 headers 字段。"""
        raw = entry.get("headers")
        if raw is None:
            return {}
        if not isinstance(raw, dict) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in raw.items()
        ):
            logger.warning(
                "MCP Server '%s' 的 headers 必须为字符串字典，跳过",
                name,
            )
            return None
        return dict(raw)

    @staticmethod
    def _expand_entry_env_refs(
        entry: dict[str, Any],
        *,
        undefined_env: _UndefinedEnvPolicy,
    ) -> dict[str, Any]:
        """递归展开配置中的环境变量引用。"""

        def _expand(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: _expand(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_expand(item) for item in value]
            if isinstance(value, str):
                return MCPConfigLoader._expand_env_tokens(value, undefined_env=undefined_env)
            return value

        return _expand(entry)

    @staticmethod
    def _expand_env_tokens(value: str, *, undefined_env: _UndefinedEnvPolicy) -> str:
        """在字符串中展开 ``$VAR``/``${VAR}``。"""

        def _replace(match: re.Match[str]) -> str:
            raw_name = match.group(1)
            name = raw_name[1:-1] if raw_name.startswith("{") else raw_name
            env_value = os.environ.get(name)
            if env_value is not None:
                return env_value
            if undefined_env == "empty":
                return ""
            if undefined_env == "keep":
                return match.group(0)
            raise ValueError(f"环境变量 '{name}' 未定义")

        return _ENV_VAR_TOKEN_PATTERN.sub(_replace, value)

    @staticmethod
    def _collect_plaintext_secret_paths(entry: dict) -> list[str]:
        found: list[str] = []

        def _append(path: str) -> None:
            if path not in found:
                found.append(path)

        def _is_env_ref(value: str) -> bool:
            return bool(_ENV_VAR_REF_PATTERN.match(value.strip()))

        def _is_sensitive_key(key: str) -> bool:
            normalized = key.strip().lower()
            return any(token in normalized for token in _SENSITIVE_KEYWORDS)

        def _scan_args(path: str, args: list[object]) -> None:
            for idx, arg in enumerate(args):
                if not isinstance(arg, str):
                    continue
                current = arg.strip()
                if not current.startswith("--"):
                    continue

                flag, sep, value = current.partition("=")
                flag_name = flag[2:].strip().lower()

                if sep:
                    if (
                        _is_sensitive_key(flag_name)
                        and value.strip()
                        and not _is_env_ref(value)
                    ):
                        _append(f"{path}[{idx}]")
                    continue

                if not _is_sensitive_key(flag_name):
                    continue

                if idx + 1 >= len(args):
                    continue
                next_arg = args[idx + 1]
                if not isinstance(next_arg, str):
                    continue
                next_value = next_arg.strip()
                if not next_value or next_value.startswith("--"):
                    continue
                if not _is_env_ref(next_value):
                    _append(f"{path}[{idx + 1}]")

        def _walk(value: object, path: str) -> None:
            if isinstance(value, dict):
                for key, sub_value in value.items():
                    if not isinstance(key, str):
                        continue
                    next_path = f"{path}.{key}" if path else key
                    if _is_sensitive_key(key) and isinstance(sub_value, str):
                        if sub_value.strip() and not _is_env_ref(sub_value):
                            _append(next_path)
                    _walk(sub_value, next_path)
                return
            if isinstance(value, list):
                if path.endswith("args"):
                    _scan_args(path, value)
                for idx, item in enumerate(value):
                    _walk(item, f"{path}[{idx}]")

        _walk(entry, "")
        return found
