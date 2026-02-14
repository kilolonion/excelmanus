"""MCP 管理器模块。

统一管理所有 MCP Server 连接和远程工具注册。
协调配置加载、连接建立、工具发现和 ToolRegistry 注册。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable

from excelmanus.mcp.client import MCPClientWrapper
from excelmanus.mcp.processes import (
    snapshot_workspace_mcp_pids,
    terminate_workspace_mcp_processes,
)
from excelmanus.tools.registry import ToolDef

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# npx 包预安装 —— 首次阻塞安装，后续后台静默更新
# ---------------------------------------------------------------------------

_MCP_MARKER_DIR = Path.home() / ".excelmanus" / ".mcp_markers"

# npx 参数中需要跳过的 flag（非包名）
_NPX_SKIP_FLAGS = {"-y", "--yes", "-p", "--package", "--no-install", "--no",
                   "--ignore-existing", "-q", "--quiet"}


def _extract_npx_package(args: list[str]) -> str | None:
    """从 npx 参数列表中提取包名（第一个非 flag 参数）。"""
    for arg in args:
        if arg in _NPX_SKIP_FLAGS or arg.startswith("-"):
            continue
        return arg
    return None


def _marker_path(pkg_spec: str) -> Path:
    """根据包名生成 marker 文件路径。"""
    safe = pkg_spec.replace("/", "__").replace("@", "_at_")
    return _MCP_MARKER_DIR / safe


def _is_package_marked(pkg_spec: str) -> bool:
    """检查包是否已标记为安装过（同时检查不带 @latest 的变体）。"""
    if _marker_path(pkg_spec).exists():
        return True
    base = pkg_spec.replace("@latest", "")
    return base != pkg_spec and _marker_path(base).exists()


def _mark_package(pkg_spec: str) -> None:
    """标记包为已安装。同时写入不带 @latest 的变体。"""
    _MCP_MARKER_DIR.mkdir(parents=True, exist_ok=True)
    _marker_path(pkg_spec).touch()
    base = pkg_spec.replace("@latest", "")
    if base != pkg_spec:
        _marker_path(base).touch()


def _strip_latest_from_args(args: list[str]) -> list[str]:
    """移除 npx 参数中的 @latest 后缀，让 npx 直接使用缓存。"""
    return [a.replace("@latest", "") if "@latest" in a else a for a in args]


async def _preinstall_npx_package(pkg_spec: str) -> bool:
    """阻塞式预安装 npx 包。通过 --package 安装但不启动服务。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "npx", "--yes", "--package", pkg_spec, "--",
            "node", "-e", "process.exit(0)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode == 0:
            return True
        logger.warning(
            "预安装 MCP 包 '%s' 失败 (rc=%d): %s",
            pkg_spec, proc.returncode,
            stderr.decode(errors="replace")[:200],
        )
        return False
    except asyncio.TimeoutError:
        logger.warning("预安装 MCP 包 '%s' 超时 (120s)", pkg_spec)
        return False
    except Exception as exc:
        logger.warning("预安装 MCP 包 '%s' 异常: %s", pkg_spec, exc)
        return False


async def _background_update_package(pkg_spec: str) -> None:
    """后台静默更新 npx 包（不阻塞主流程）。"""
    try:
        # 确保使用 @latest 以检查最新版本
        update_spec = pkg_spec if "@latest" in pkg_spec else f"{pkg_spec}@latest"
        proc = await asyncio.create_subprocess_exec(
            "npx", "--yes", "--package", update_spec, "--",
            "node", "-e", "process.exit(0)",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=120)
        logger.debug("后台更新 MCP 包 '%s' 完成", pkg_spec)
    except Exception:
        logger.debug("后台更新 MCP 包 '%s' 失败，已忽略", pkg_spec, exc_info=True)

# ---------------------------------------------------------------------------
# 工具名前缀映射
# ---------------------------------------------------------------------------
# 由于 server_name 中的 `-` 被替换为 `_`，而 tool_name 本身也可能包含 `_`，
# 仅凭字符串切分无法可靠地还原 server_name 和 tool_name。
# 因此使用模块级注册表记录 prefixed_name → (server_name, tool_name) 的映射，
# 在 add_tool_prefix 时写入，在 parse_tool_prefix 时查找，保证 round-trip 精确。
# ---------------------------------------------------------------------------

_PREFIX = "mcp_"

# prefixed_name -> (normalized_server_name, original_tool_name)
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

    提取 ``content`` 列表中所有 ``type == "text"`` 的文本并拼接。

    Args:
        mcp_result: MCP ``CallToolResult`` 对象（duck typing）。

    Returns:
        拼接后的文本字符串；若无 text 内容则返回空字符串。
    """
    parts: list[str] = []
    for item in getattr(mcp_result, "content", []):
        if getattr(item, "type", None) == "text":
            text = getattr(item, "text", "")
            if text:
                parts.append(text)
    return "\n".join(parts)


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
    )


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
        self._managed_workspace_pids: set[int] = set()
        # 初始化后填充：白名单中的 MCP 工具（prefixed_name 列表）
        self._auto_approved_tools: list[str] = []

    async def initialize(self, registry: "ToolRegistry") -> None:
        """加载配置 → 预安装包 → 连接所有 Server → 注册远程工具到 ToolRegistry。

        流程：
        1. 调用 MCPConfigLoader.load() 加载配置
        2. 无配置时直接返回
        3. npx 包预安装：首次阻塞安装，已有则去除 @latest 加速启动并后台更新
        4. 逐个创建 MCPClientWrapper 并连接（连接失败记录 ERROR 并跳过）
        5. 连接成功后发现工具，转换为 ToolDef
        6. 检查工具名冲突，冲突则跳过并记录 WARNING
        7. 批量注册所有不冲突的工具到 registry

        任何单个 Server 的失败不影响其余 Server。
        """
        from excelmanus.mcp.config import MCPConfigLoader

        configs = MCPConfigLoader.load(workspace_root=self._workspace_root)
        if not configs:
            logger.debug("无 MCP Server 配置，跳过初始化")
            return

        # ── 第一阶段：npx 包预安装 ───────────────────────
        background_updates: list[str] = []
        for cfg in configs:
            if cfg.transport != "stdio" or cfg.command not in ("npx", "npx.cmd"):
                continue
            pkg_spec = _extract_npx_package(cfg.args)
            if not pkg_spec:
                continue

            if not _is_package_marked(pkg_spec):
                # 首次安装：阻塞等待
                logger.info("首次安装 MCP 包 '%s'，请稍候...", pkg_spec)
                success = await _preinstall_npx_package(pkg_spec)
                if success:
                    _mark_package(pkg_spec)
                    logger.info("MCP 包 '%s' 安装完成", pkg_spec)
                else:
                    logger.warning(
                        "MCP 包 '%s' 预安装失败，将在连接时重试", pkg_spec
                    )
            else:
                # 已安装：去除 @latest 以加速启动，后台静默更新
                cfg.args = _strip_latest_from_args(cfg.args)
                background_updates.append(pkg_spec)

        # 启动后台更新任务（不阻塞后续连接）
        for pkg_spec in background_updates:
            asyncio.create_task(
                _background_update_package(pkg_spec),
                name=f"mcp-update-{pkg_spec}",
            )

        # ── 第二阶段：连接 Server 并注册工具 ──────────────
        # 收集所有待注册的 ToolDef
        all_tool_defs: list[ToolDef] = []
        auto_approved_names: list[str] = []
        known_workspace_pids = snapshot_workspace_mcp_pids(self._workspace_root)

        for cfg in configs:
            client = MCPClientWrapper(cfg)
            try:
                await client.connect()
            except Exception as exc:
                logger.error(
                    "连接 MCP Server '%s' 失败: %s", cfg.name, exc
                )
                continue

            if cfg.transport == "stdio":
                current_workspace_pids = snapshot_workspace_mcp_pids(
                    self._workspace_root
                )
                spawned_pids = current_workspace_pids - known_workspace_pids
                if spawned_pids:
                    client.bind_managed_pids(spawned_pids)
                    self._managed_workspace_pids.update(spawned_pids)
                known_workspace_pids = current_workspace_pids

            # 连接成功，记录到 _clients
            self._clients[cfg.name] = client

            # 发现远程工具
            try:
                mcp_tools = await client.discover_tools()
            except Exception as exc:
                logger.error(
                    "发现 MCP Server '%s' 的工具失败: %s", cfg.name, exc
                )
                continue

            if cfg.transport == "stdio":
                current_workspace_pids = snapshot_workspace_mcp_pids(
                    self._workspace_root
                )
                spawned_pids = current_workspace_pids - known_workspace_pids
                if spawned_pids:
                    bound_pids = client.managed_pids | spawned_pids
                    client.bind_managed_pids(bound_pids)
                    self._managed_workspace_pids.update(spawned_pids)
                known_workspace_pids = current_workspace_pids

            # 转换为 ToolDef，检查冲突
            existing_names = set(registry.get_tool_names())
            # 也要排除本轮已收集的工具名
            pending_names = {td.name for td in all_tool_defs}

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
                if tool_def.name in pending_names:
                    logger.warning(
                        "MCP 工具 '%s' (server=%s) 与其他 MCP 工具冲突，跳过",
                        tool_def.name,
                        cfg.name,
                    )
                    continue
                all_tool_defs.append(tool_def)
                pending_names.add(tool_def.name)

                # 收集白名单：autoApprove 含 "*" 或匹配原始工具名
                original_name: str = getattr(tool, "name", "")
                if "*" in cfg.auto_approve or original_name in cfg.auto_approve:
                    auto_approved_names.append(tool_def.name)

        # 批量注册
        if all_tool_defs:
            registry.register_tools(all_tool_defs)

        # 保存白名单信息
        self._auto_approved_tools = auto_approved_names

        logger.info(
            "MCP 初始化完成：%d 个 Server 已连接，%d 个远程工具已注册",
            len(self._clients),
            len(all_tool_defs),
        )

    async def shutdown(self) -> None:
        """关闭所有 MCP Server 连接。

        逐个调用 client.close()，关闭失败时记录 WARNING 但不抛异常。
        """
        managed_pids: set[int] = set(self._managed_workspace_pids)
        for name, client in self._clients.items():
            raw_pids = getattr(client, "managed_pids", set())
            if isinstance(raw_pids, (set, list, tuple)):
                for pid in raw_pids:
                    try:
                        parsed = int(pid)
                    except (TypeError, ValueError):
                        continue
                    if parsed > 0:
                        managed_pids.add(parsed)
            try:
                await client.close()
            except BaseException as exc:
                logger.warning(
                    "关闭 MCP Server '%s' 连接失败: %s", name, exc
                )
        self._clients.clear()
        self._managed_workspace_pids.clear()

        if managed_pids:
            remaining = terminate_workspace_mcp_processes(
                workspace_root=self._workspace_root,
                candidate_pids=managed_pids,
            )
            if remaining:
                logger.warning(
                    "MCP 兜底清理后仍有进程存活: %s",
                    sorted(remaining),
                )
            else:
                logger.debug(
                    "MCP 兜底清理完成，共处理 %d 个本地进程",
                    len(managed_pids),
                )

        logger.debug("所有 MCP Server 连接已关闭")

    @property
    def connected_servers(self) -> list[str]:
        """返回已连接的 Server 名称列表。"""
        return list(self._clients.keys())

    @property
    def auto_approved_tools(self) -> list[str]:
        """返回白名单中的 MCP 工具名列表（prefixed_name）。"""
        return list(self._auto_approved_tools)

    def get_server_info(self) -> list[dict[str, Any]]:
        """返回所有已连接 Server 的摘要信息。

        每个元素包含：
        - name: Server 名称
        - transport: 传输方式（stdio/sse）
        - tool_count: 已注册的工具数量
        - tools: 工具名称列表（原始名称，不含前缀）
        """
        info: list[dict[str, Any]] = []
        for name, client in self._clients.items():
            tools = getattr(client, "_tools", [])
            tool_names = [getattr(t, "name", str(t)) for t in tools]
            info.append({
                "name": name,
                "transport": getattr(client._config, "transport", "unknown"),
                "tool_count": len(tool_names),
                "tools": tool_names,
            })
        return info

    def generate_skillpacks(self) -> list["Skillpack"]:
        """从已连接 MCP Server 的工具元数据自动生成 Skillpack。

        每个已连接的 MCP Server 生成一个 Skillpack：
        - name: ``mcp_{normalized_server_name}``
        - allowed_tools: ``mcp:{server}:*``（通配符选择器，运行时展开）
        - description / instructions: 从工具名称和描述自动派生
        - source: ``"system"``
        - priority: 3（介于专项 skill 5-9 和兜底 skill 1 之间）

        Returns:
            自动生成的 Skillpack 列表。无已连接 Server 时返回空列表。
        """
        from excelmanus.skillpacks.models import Skillpack

        generated: list[Skillpack] = []
        for server_name, client in self._clients.items():
            mcp_tools = getattr(client, "_tools", [])
            if not mcp_tools:
                continue

            normalized = _normalize_server_name(server_name)
            skill_name = f"mcp_{normalized}"

            # 收集工具名和描述
            tool_summaries: list[str] = []
            for t in mcp_tools:
                t_name = getattr(t, "name", str(t))
                t_desc = getattr(t, "description", "") or ""
                summary = f"- `{t_name}`: {t_desc}" if t_desc else f"- `{t_name}`"
                tool_summaries.append(summary)

            tool_list_text = "\n".join(tool_summaries)
            tool_count = len(mcp_tools)

            # 生成描述：server 名 + 工具数量概览
            description = (
                f"MCP Server「{server_name}」提供的 {tool_count} 个远程工具"
            )

            # 生成执行指引
            instructions = (
                f"通过 MCP 协议调用「{server_name}」服务器的远程工具。\n"
                f"\n"
                f"可用工具（共 {tool_count} 个）：\n"
                f"{tool_list_text}\n"
                f"\n"
                f"工具名在调用时带 `mcp_{normalized}_` 前缀，"
                f"例如 `mcp_{normalized}_{getattr(mcp_tools[0], 'name', 'example')}`。"
            )

            skillpack = Skillpack(
                name=skill_name,
                description=description,
                allowed_tools=[f"mcp:{server_name}:*"],
                triggers=[],
                instructions=instructions,
                source="system",
                root_dir="",
                priority=3,
                version="1.0.0",
                disable_model_invocation=False,
                user_invocable=True,
            )
            generated.append(skillpack)
            logger.info(
                "自动生成 MCP Skillpack '%s'（%d 个工具）",
                skill_name,
                tool_count,
            )

        return generated
