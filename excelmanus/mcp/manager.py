"""MCP 管理器模块。

统一管理所有 MCP Server 连接和远程工具注册。
协调配置加载、连接建立、工具发现和 ToolRegistry 注册。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from excelmanus.mcp.client import MCPClientWrapper
from excelmanus.mcp.processes import (
    snapshot_workspace_mcp_pids,
    terminate_workspace_mcp_processes,
)
from excelmanus.tools.registry import ToolDef

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 工具名前缀映射
# ---------------------------------------------------------------------------
# 由于 server_name 中的 `-` 被替换为 `_`，而 tool_name 本身也可能包含 `_`，
# 仅凭字符串切分无法可靠地还原 server_name 和 tool_name。
# 因此使用模块级注册表记录 prefixed_name → (server_name, tool_name) 的映射，
# 在 add_tool_prefix 时写入，在 parse_tool_prefix 时查找，保证 round-trip 精确。
# ---------------------------------------------------------------------------

_PREFIX = "mcp_"

# prefixed_name -> (normalized_server_name, original_tool_name) 的映射注册表
_prefix_registry: dict[str, tuple[str, str]] = {}


def _normalize_server_name(server_name: str) -> str:
    """将 server_name 中的 `-` 替换为 `_`，保持 Python 标识符兼容。"""
    return server_name.replace("-", "_")


def add_tool_prefix(server_name: str, tool_name: str) -> str:
    """为远程工具名添加 MCP 前缀。

    格式：``mcp_{normalized_server_name}_{original_tool_name}``

    同时将映射关系写入模块级注册表，供 :func:`parse_tool_prefix` 还原。

    Args:
        server_name: MCP Server 名称（原始，可含 ``-``）。
        tool_name: 远程工具的原始名称。

    Returns:
        带前缀的工具名。
    """
    normalized = _normalize_server_name(server_name)
    prefixed = f"{_PREFIX}{normalized}_{tool_name}"
    _prefix_registry[prefixed] = (normalized, tool_name)
    return prefixed


def parse_tool_prefix(prefixed_name: str) -> tuple[str, str]:
    """从带前缀的工具名中还原 server_name 和 tool_name。

    优先从注册表精确查找；若注册表中不存在，则回退到字符串切分
    （取第一个 ``_`` 之后、第二个 ``_`` 之前作为 server_name，其余为 tool_name）。

    注意：回退模式下，若 server_name 包含 ``_``（由 ``-`` 转换而来），
    切分结果可能不准确。建议始终先通过 :func:`add_tool_prefix` 注册。

    Args:
        prefixed_name: 带 ``mcp_`` 前缀的工具名。

    Returns:
        ``(normalized_server_name, original_tool_name)`` 二元组。

    Raises:
        ValueError: 名称不以 ``mcp_`` 开头或格式不合法。
    """
    if not prefixed_name.startswith(_PREFIX):
        raise ValueError(
            f"工具名 '{prefixed_name}' 不以 '{_PREFIX}' 开头，无法解析"
        )

    # 优先从注册表精确查找
    if prefixed_name in _prefix_registry:
        return _prefix_registry[prefixed_name]

    # 回退：字符串切分（去掉 "mcp_" 后，以第一个 "_" 分割）
    remainder = prefixed_name[len(_PREFIX):]
    sep_idx = remainder.find("_")
    if sep_idx <= 0:
        raise ValueError(
            f"工具名 '{prefixed_name}' 格式不合法，"
            f"期望 'mcp_{{server}}_{{name}}'"
        )
    return remainder[:sep_idx], remainder[sep_idx + 1:]


# ---------------------------------------------------------------------------
# 工具结果转换
# ---------------------------------------------------------------------------


def format_tool_result(mcp_result: Any) -> str:
    """将 MCP 工具调用结果转换为字符串。

    优先级：
    1. ``content`` 内 ``type == "text"`` 的文本拼接；
    2. ``structuredContent``（JSON 序列化）；
    3. ``resource`` / 其它内容块的可读降级摘要。

    Args:
        mcp_result: MCP ``CallToolResult`` 对象（duck typing）。

    Returns:
        最终文本结果；无可读内容时返回空字符串。
    """
    text_parts: list[str] = []
    fallback_parts: list[str] = []
    for item in getattr(mcp_result, "content", []):
        item_type = getattr(item, "type", None)
        if item_type == "text":
            text = getattr(item, "text", "")
            if isinstance(text, str) and text:
                text_parts.append(text)
            continue

        if item_type == "resource":
            resource = getattr(item, "resource", None)
            uri = getattr(resource, "uri", None) if resource is not None else None
            mime = (
                getattr(resource, "mimeType", None)
                if resource is not None
                else None
            )
            inline_text = (
                getattr(resource, "text", None)
                if resource is not None
                else None
            )
            if isinstance(inline_text, str) and inline_text.strip():
                fallback_parts.append(inline_text.strip())
            else:
                label = f"resource uri={uri}" if uri else "resource"
                if mime:
                    label = f"{label} mime={mime}"
                fallback_parts.append(f"[{label}]")
            continue

        if item_type:
            fallback_parts.append(f"[{item_type} content]")

    if text_parts:
        return "\n".join(text_parts)

    structured = getattr(mcp_result, "structuredContent", None)
    if structured not in (None, ""):
        try:
            return json.dumps(
                structured,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        except TypeError:
            return str(structured)

    return "\n".join(fallback_parts)


_EXCEL_SERVER_NAME = "excel"
_EXCEL_ABSOLUTE_PATH_ARG = "fileAbsolutePath"


def _normalize_excel_mcp_absolute_path(path_value: Any, *, workspace_root: str) -> Any:
    """规范化 Excel MCP 的绝对路径参数。

    规则：
    1. 相对路径 → 基于 workspace_root 转绝对路径；
    2. 绝对路径但文件不存在 → 若工作区存在同名文件，则回退到该文件；
    3. 其他情况保持原值。
    """
    if not isinstance(path_value, str):
        return path_value

    raw = path_value.strip()
    if not raw:
        return path_value

    try:
        workspace = Path(workspace_root).expanduser()
        if not workspace.is_absolute():
            workspace = Path.cwd() / workspace
        workspace = workspace.resolve(strict=False)

        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            return str((workspace / candidate).resolve(strict=False))

        resolved = candidate.resolve(strict=False)
        if resolved.is_file():
            return str(resolved)

        # 某些模型会拼出不存在的临时目录绝对路径，尝试按文件名回落到工作区。
        fallback = (workspace / resolved.name).resolve(strict=False)
        if fallback.is_file():
            logger.warning(
                "检测到不可用 Excel 绝对路径，已回落到工作区同名文件: %s -> %s",
                resolved,
                fallback,
            )
            return str(fallback)
        return str(resolved)
    except OSError:
        return path_value


def _adapt_mcp_call_arguments(
    *,
    server_name: str,
    arguments: dict[str, Any],
    workspace_root: str,
) -> dict[str, Any]:
    """按 server 规则预处理 MCP 参数。"""
    if _normalize_server_name(server_name) != _EXCEL_SERVER_NAME:
        return arguments

    raw_path = arguments.get(_EXCEL_ABSOLUTE_PATH_ARG)
    normalized_path = _normalize_excel_mcp_absolute_path(
        raw_path,
        workspace_root=workspace_root,
    )
    if normalized_path == raw_path:
        return arguments
    patched = dict(arguments)
    patched[_EXCEL_ABSOLUTE_PATH_ARG] = normalized_path
    return patched


# ---------------------------------------------------------------------------
# 异步到同步桥接
# ---------------------------------------------------------------------------


def _make_tool_func(
    client: "MCPClientWrapper",
    server_name: str,
    original_name: str,
    timeout: int,
    workspace_root: str,
) -> Callable[..., str]:
    """创建同步包装函数，内部通过 event loop 执行异步 MCP 工具调用。

    Args:
        client: MCP 客户端封装实例。
        server_name: MCP Server 名称（原始，可含 ``-``）。
        original_name: 远程工具的原始名称（不含前缀）。
        timeout: 调用超时秒数。
        workspace_root: 当前工作区根目录，用于路径规范化。

    Returns:
        同步可调用对象，签名为 ``(**kwargs) -> str``。
    """

    def tool_func(**kwargs: Any) -> str:
        async def _call() -> str:
            safe_kwargs = _adapt_mcp_call_arguments(
                server_name=server_name,
                arguments=kwargs,
                workspace_root=workspace_root,
            )
            result = await asyncio.wait_for(
                client.call_tool(original_name, safe_kwargs),
                timeout=timeout,
            )
            return format_tool_result(result)

        # 判断当前是否已有运行中的 event loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # 在已有 event loop 中，使用线程池执行 asyncio.run
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _call())
                return future.result()
        else:
            return asyncio.run(_call())

    return tool_func


# ---------------------------------------------------------------------------
# MCP 工具定义 → ToolDef 转换
# ---------------------------------------------------------------------------


def make_tool_def(
    server_name: str,
    client: "MCPClientWrapper",
    mcp_tool: Any,
    workspace_root: str = ".",
) -> ToolDef:
    """将 MCP 工具定义转换为 ToolDef。

    映射规则：
    - ``name``：添加 ``mcp_{server}_`` 前缀
    - ``description``：前缀 ``[MCP:{server}]``，后接原始描述
    - ``input_schema``：直接映射（已是 JSON Schema）
    - ``func``：异步调用闭包（同步包装）
    - ``max_result_chars``：默认 5000（远程工具结果可能较长）

    Args:
        server_name: MCP Server 名称（原始，可含 ``-``）。
        client: 对应的 MCPClientWrapper 实例。
        mcp_tool: MCP 工具定义对象（duck typing），需具有
            ``name``、``description``、``inputSchema`` 属性。
        workspace_root: 当前工作区根目录。

    Returns:
        转换后的 ToolDef 实例。
    """
    original_name: str = mcp_tool.name
    description: str = mcp_tool.description or ""
    input_schema: dict[str, Any] = mcp_tool.inputSchema or {}

    # 获取超时配置（从 client 的 config 中读取，默认 30 秒）
    timeout: int = getattr(getattr(client, "_config", None), "timeout", 30)

    prefixed_name = add_tool_prefix(server_name, original_name)
    tagged_description = f"[MCP:{server_name}] {description}"

    func = _make_tool_func(
        client,
        server_name,
        original_name,
        timeout,
        workspace_root,
    )

    return ToolDef(
        name=prefixed_name,
        description=tagged_description,
        input_schema=input_schema,
        func=func,
        max_result_chars=5000,
        write_effect="unknown",
    )


_ServerStatus = Literal["ready", "connect_failed", "discover_failed"]


@dataclass
class _ServerRuntimeState:
    """MCP Server 运行时状态。"""

    name: str
    transport: str
    status: _ServerStatus
    last_error: str | None = None
    tool_names: list[str] = field(default_factory=list)
    init_ms: int = 0


def _short_error(exc: BaseException) -> str:
    """提取简短错误文本，避免日志/状态面板过长。"""
    text = str(exc).strip()
    if not text:
        text = exc.__class__.__name__
    if len(text) > 300:
        return text[:297] + "..."
    return text


# ---------------------------------------------------------------------------
# MCPManager — 多 Server 连接与工具注册管理器
# ---------------------------------------------------------------------------


class MCPManager:
    """MCP Client 管理器，协调多个 MCP Server 的连接和工具注册。

    典型用法::

        manager = MCPManager(workspace_root=".")
        await manager.initialize(registry)
        # ... Agent 运行 ...
        await manager.shutdown()
    """

    def __init__(self, workspace_root: str = ".") -> None:
        self._workspace_root = workspace_root
        self._clients: dict[str, MCPClientWrapper] = {}
        self._leaked_clients: list[MCPClientWrapper] = []  # discover 失败且 close 异常的僵尸连接
        self._managed_workspace_pids: set[int] = set()
        self._managed_workspace_pids_by_state_dir: dict[str | None, set[int]] = {}
        self._server_states: dict[str, _ServerRuntimeState] = {}
        self._initialize_lock = asyncio.Lock()
        self._initialized = False
        # 初始化后填充：白名单中的 MCP 工具（prefixed_name 列表）
        self._auto_approved_tools: list[str] = []
        # 后台安装任务
        self._background_tasks: list[asyncio.Task] = []
        self._registry: "ToolRegistry | None" = None

    async def initialize(self, registry: "ToolRegistry") -> None:
        """加载配置 → 连接所有 Server → 注册远程工具到 ToolRegistry。

        流程：
        1. 调用 MCPConfigLoader.load() 加载配置
        2. 对 npx 命令检查本地缓存：
           - 缓存命中 → 立即连接
           - 缓存未命中 → 后台安装，不阻塞主启动
        3. 连接成功后发现工具，转换为 ToolDef
        4. 检查工具名冲突，冲突则跳过并记录 WARNING
        5. 批量注册所有不冲突的工具到 registry

        任何单个 Server 的失败不影响其余 Server。
        """
        async with self._initialize_lock:
            if self._initialized:
                logger.debug("MCP 已初始化，跳过重复初始化")
                return

            from excelmanus.mcp.config import MCPConfigLoader

            self._clients.clear()
            self._leaked_clients.clear()
            self._managed_workspace_pids.clear()
            self._managed_workspace_pids_by_state_dir.clear()
            self._auto_approved_tools = []
            self._server_states.clear()
            self._registry = registry

            configs = MCPConfigLoader.load(workspace_root=self._workspace_root)
            if not configs:
                self._initialized = True
                logger.debug("无 MCP Server 配置，跳过初始化")
                return

            # ── 将 npx 命令解析为缓存的二进制，避免每次启动重复安装 ──
            # 缓存命中：立即可用；缓存未命中：推入后台安装队列
            from excelmanus.mcp.npx_cache import try_resolve_from_cache

            ready_configs: list[type(configs[0])] = []
            deferred_configs: list[type(configs[0])] = []

            for cfg in configs:
                resolved = try_resolve_from_cache(cfg)
                if resolved is not None:
                    ready_configs.append(resolved)
                else:
                    # 缓存未命中 → 标记为 installing，推迟到后台
                    self._server_states[cfg.name] = _ServerRuntimeState(
                        name=cfg.name,
                        transport=cfg.transport,
                        status="installing",
                    )
                    deferred_configs.append(cfg)

            # ── 连接缓存命中的 Server 并注册工具 ──────────
            all_tool_defs: list[ToolDef] = []
            auto_approved_names: list[str] = []
            batch_pending_names: set[str] = set()

            for cfg in ready_configs:
                tool_defs, approved = await self._connect_and_register_server(
                    cfg, registry, batch_pending_names=batch_pending_names,
                )
                all_tool_defs.extend(tool_defs)
                auto_approved_names.extend(approved)
                batch_pending_names.update(td.name for td in tool_defs)

            # 批量注册
            if all_tool_defs:
                registry.register_tools(all_tool_defs)

            self._auto_approved_tools = auto_approved_names
            self._initialized = True

            ready_count = sum(
                1
                for item in self._server_states.values()
                if item.status == "ready"
            )
            total_count = len(self._server_states)
            deferred_count = len(deferred_configs)

            if deferred_count:
                logger.info(
                    "MCP 初始化完成：%d/%d 个 Server 就绪，%d 个远程工具已注册"
                    "｜%d 个 Server 后台安装中",
                    ready_count,
                    total_count,
                    len(all_tool_defs),
                    deferred_count,
                )
            else:
                logger.info(
                    "MCP 初始化完成：%d/%d 个 Server 就绪，%d 个远程工具已注册",
                    ready_count,
                    total_count,
                    len(all_tool_defs),
                )

            # ── 后台安装 npx 包并延迟连接 ──────────
            if deferred_configs:
                task = asyncio.create_task(
                    self._deferred_install_and_connect(deferred_configs, registry),
                    name="mcp-npx-deferred-install",
                )
                self._background_tasks.append(task)

    async def _connect_and_register_server(
        self,
        cfg: "MCPServerConfig",
        registry: "ToolRegistry",
        *,
        batch_pending_names: set[str] | None = None,
    ) -> tuple[list[ToolDef], list[str]]:
        """连接单个 Server、发现工具并收集 ToolDef。

        Args:
            batch_pending_names: 同一批次中已收集但尚未注册的工具名集合，
                用于跨 Server 去重。

        Returns:
            (tool_defs, auto_approved_names) — 尚未注册到 registry，由调用方批量注册。
        """
        from excelmanus.mcp.config import MCPServerConfig as _Cfg  # noqa: F811

        started = time.monotonic()
        state = self._server_states.get(cfg.name)
        if state is None:
            state = _ServerRuntimeState(
                name=cfg.name,
                transport=cfg.transport,
                status="connect_failed",
            )
            self._server_states[cfg.name] = state
        else:
            state.transport = cfg.transport
            state.status = "connect_failed"

        client = MCPClientWrapper(cfg)
        known_workspace_pids: set[int] = set()
        if cfg.transport == "stdio":
            known_workspace_pids = snapshot_workspace_mcp_pids(
                self._workspace_root,
                state_dir=cfg.state_dir,
            )
        try:
            await client.connect()
        except Exception as exc:
            state.status = "connect_failed"
            state.last_error = _short_error(exc)
            state.init_ms = int((time.monotonic() - started) * 1000)
            logger.error(
                "连接 MCP Server '%s' 失败: %s",
                cfg.name,
                state.last_error,
            )
            return [], []

        if cfg.transport == "stdio":
            current_workspace_pids = snapshot_workspace_mcp_pids(
                self._workspace_root,
                state_dir=cfg.state_dir,
            )
            spawned_pids = current_workspace_pids - known_workspace_pids
            if spawned_pids:
                if hasattr(client, "bind_managed_pids"):
                    client.bind_managed_pids(spawned_pids)
                self._track_managed_pids(
                    spawned_pids,
                    state_dir=cfg.state_dir,
                )
            known_workspace_pids = current_workspace_pids

        # 发现远程工具
        try:
            mcp_tools = await client.discover_tools()
        except Exception as exc:
            state.status = "discover_failed"
            state.last_error = _short_error(exc)
            state.init_ms = int((time.monotonic() - started) * 1000)
            logger.error(
                "发现 MCP Server '%s' 的工具失败: %s",
                cfg.name,
                state.last_error,
            )
            try:
                await client.close()
            except BaseException:
                logger.debug(
                    "discover 失败后关闭 MCP Server '%s' 时异常，加入待清理列表",
                    cfg.name,
                    exc_info=True,
                )
                self._leaked_clients.append(client)
            return [], []

        if cfg.transport == "stdio":
            current_workspace_pids = snapshot_workspace_mcp_pids(
                self._workspace_root,
                state_dir=cfg.state_dir,
            )
            spawned_pids = current_workspace_pids - known_workspace_pids
            if spawned_pids:
                existing = getattr(client, "managed_pids", set())
                bound_pids = set(existing) | spawned_pids
                if hasattr(client, "bind_managed_pids"):
                    client.bind_managed_pids(bound_pids)
                self._track_managed_pids(
                    spawned_pids,
                    state_dir=cfg.state_dir,
                )

        # 转换为 ToolDef，检查冲突
        existing_names = set(registry.get_tool_names())
        if batch_pending_names:
            existing_names |= batch_pending_names
        tool_defs: list[ToolDef] = []
        auto_approved: list[str] = []
        local_pending: set[str] = set()
        tool_names: list[str] = []

        for tool in mcp_tools:
            tool_def = make_tool_def(
                cfg.name,
                client,
                tool,
                workspace_root=self._workspace_root,
            )
            if tool_def.name in existing_names:
                logger.warning(
                    "MCP 工具 '%s' (server=%s) 与已注册工具冲突，跳过",
                    tool_def.name,
                    cfg.name,
                )
                continue
            if tool_def.name in local_pending:
                logger.warning(
                    "MCP 工具 '%s' (server=%s) 与其他 MCP 工具冲突，跳过",
                    tool_def.name,
                    cfg.name,
                )
                continue
            tool_defs.append(tool_def)
            local_pending.add(tool_def.name)
            original_name: str = getattr(tool, "name", "")
            if original_name:
                tool_names.append(original_name)

            # 收集白名单：autoApprove 含 "*" 或匹配原始工具名
            if "*" in cfg.auto_approve or original_name in cfg.auto_approve:
                auto_approved.append(tool_def.name)

        self._clients[cfg.name] = client
        state.status = "ready"
        state.last_error = None
        state.tool_names = tool_names
        state.init_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "MCP Server 就绪: server=%s status=%s transport=%s init_ms=%d pid_count=%d",
            cfg.name,
            state.status,
            cfg.transport,
            state.init_ms,
            len(getattr(client, "managed_pids", set())),
        )
        return tool_defs, auto_approved

    async def _deferred_install_and_connect(
        self,
        deferred_configs: list,
        registry: "ToolRegistry",
    ) -> None:
        """后台安装 npx 包并连接 Server（不阻塞主启动）。"""
        from excelmanus.mcp.npx_cache import resolve_npx_config

        # 并发安装所有待安装的包
        resolved = await asyncio.gather(
            *(resolve_npx_config(cfg) for cfg in deferred_configs),
            return_exceptions=True,
        )

        for cfg_or_exc, original_cfg in zip(resolved, deferred_configs):
            if isinstance(cfg_or_exc, BaseException):
                state = self._server_states.get(original_cfg.name)
                if state:
                    state.status = "install_failed"
                    state.last_error = _short_error(cfg_or_exc)
                logger.error(
                    "MCP Server '%s' 后台安装失败: %s",
                    original_cfg.name,
                    cfg_or_exc,
                )
                continue

            cfg = cfg_or_exc
            tool_defs, approved = await self._connect_and_register_server(
                cfg, registry,
            )
            if tool_defs:
                registry.register_tools(tool_defs)
            if approved:
                self._auto_approved_tools.extend(approved)

        # 输出延迟连接汇总
        ready_count = sum(
            1
            for item in self._server_states.values()
            if item.status == "ready"
        )
        logger.info(
            "MCP 后台安装完成：%d/%d 个 Server 就绪",
            ready_count,
            len(self._server_states),
        )

    def _track_managed_pids(
        self,
        pids: set[int],
        *,
        state_dir: str | None,
    ) -> None:
        valid_pids: set[int] = set()
        for pid in pids:
            try:
                parsed = int(pid)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                valid_pids.add(parsed)
        if not valid_pids:
            return
        self._managed_workspace_pids.update(valid_pids)
        grouped = self._managed_workspace_pids_by_state_dir.setdefault(state_dir, set())
        grouped.update(valid_pids)

    async def shutdown(self) -> None:
        """关闭所有 MCP Server 连接。

        逐个调用 client.close()，关闭失败时记录 WARNING 但不抛异常。
        """
        async with self._initialize_lock:
            # 取消未完成的后台安装任务
            for task in self._background_tasks:
                if not task.done():
                    task.cancel()
            if self._background_tasks:
                await asyncio.gather(*self._background_tasks, return_exceptions=True)
                self._background_tasks.clear()
            grouped_managed_pids: dict[str | None, set[int]] = {
                key: set(value)
                for key, value in self._managed_workspace_pids_by_state_dir.items()
            }
            if self._managed_workspace_pids:
                grouped_managed_pids.setdefault(None, set()).update(
                    self._managed_workspace_pids
                )

            for name, client in list(self._clients.items()):
                state_dir = getattr(getattr(client, "_config", None), "state_dir", None)
                raw_pids = getattr(client, "managed_pids", set())
                if isinstance(raw_pids, (set, list, tuple)):
                    bucket = grouped_managed_pids.setdefault(state_dir, set())
                    for pid in raw_pids:
                        try:
                            parsed = int(pid)
                        except (TypeError, ValueError):
                            continue
                        if parsed > 0:
                            bucket.add(parsed)
                try:
                    await client.close()
                except BaseException as exc:
                    logger.warning(
                        "关闭 MCP Server '%s' 连接失败: %s", name, exc
                    )
            self._clients.clear()
            self._managed_workspace_pids.clear()
            self._managed_workspace_pids_by_state_dir.clear()
            self._server_states.clear()

            # 清理 discover 失败时未能关闭的僵尸连接
            for client in self._leaked_clients:
                try:
                    await client.close()
                except BaseException:
                    logger.debug("关闭僵尸 MCP 连接失败", exc_info=True)
            self._leaked_clients.clear()

            total_candidates = 0
            total_remaining = 0
            for state_dir, managed_pids in grouped_managed_pids.items():
                if not managed_pids:
                    continue
                total_candidates += len(managed_pids)
                remaining = terminate_workspace_mcp_processes(
                    workspace_root=self._workspace_root,
                    candidate_pids=managed_pids,
                    state_dir=state_dir,
                )
                if remaining:
                    total_remaining += len(remaining)
                    logger.warning(
                        "MCP 兜底清理后仍有进程存活(state_dir=%s): %s",
                        state_dir or "<default>",
                        sorted(remaining),
                    )
            if total_candidates:
                recovered_count = max(0, total_candidates - total_remaining)
                logger.info(
                    "MCP 进程回收结果: pid_count=%d recovered_count=%d remaining_count=%d",
                    total_candidates,
                    recovered_count,
                    total_remaining,
                )

            self._initialized = False
            logger.debug("所有 MCP Server 连接已关闭")

    @property
    def connected_servers(self) -> list[str]:
        """返回已连接的 Server 名称列表。"""
        return list(self._clients.keys())

    @property
    def is_initialized(self) -> bool:
        """是否已完成初始化流程（包含无配置场景）。"""
        return self._initialized

    @property
    def auto_approved_tools(self) -> list[str]:
        """返回白名单中的 MCP 工具名列表（prefixed_name）。"""
        return list(self._auto_approved_tools)

    def get_server_info(self) -> list[dict[str, Any]]:
        """返回所有已尝试初始化的 Server 摘要信息。

        每个元素包含：
        - name: Server 名称
        - transport: 传输方式（stdio/sse/streamable_http）
        - status: ``ready`` / ``connect_failed`` / ``discover_failed``
        - tool_count: 已发现并注册的工具数量
        - tools: 工具名称列表（原始名称，不含前缀）
        - last_error: 最近一次错误摘要（成功时为 ``None``）
        """
        info: list[dict[str, Any]] = []
        for name, state in sorted(self._server_states.items()):
            info.append({
                "name": name,
                "transport": state.transport,
                "status": state.status,
                "tool_count": len(state.tool_names),
                "tools": list(state.tool_names),
                "last_error": state.last_error,
                "init_ms": state.init_ms,
            })
        return info
