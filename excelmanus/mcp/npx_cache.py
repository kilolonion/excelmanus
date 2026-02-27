"""NPX 包缓存模块。

将 ``npx`` 命令转换为直接二进制调用，避免每次启动都重新检测/安装包。
首次运行时通过 ``npm install`` 将包安装到持久化缓存目录，
后续启动直接使用缓存的二进制，跳过 npx。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from excelmanus.mcp.config import MCPServerConfig

logger = logging.getLogger(__name__)

# 持久化缓存目录
_DEFAULT_CACHE_DIR = Path.home() / ".excelmanus" / "mcp_npx_cache"


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def try_resolve_from_cache(
    config: MCPServerConfig,
    *,
    cache_dir: Path | None = None,
) -> MCPServerConfig | None:
    """仅检查缓存，不触发安装。

    - 非 ``npx`` 命令：原样返回。
    - 缓存命中：返回重写后的 config。
    - 缓存未命中：返回 ``None``（调用方应安排后台安装）。
    """
    if config.command != "npx":
        return config

    package, extra_args = _parse_npx_args(config.args)
    if not package:
        return config

    cache = cache_dir or _DEFAULT_CACHE_DIR
    pkg_dir = cache / _safe_dir_name(package)
    bin_dir = pkg_dir / "node_modules" / ".bin"

    bin_path = _find_bin(bin_dir, _bin_name_from_package(package))
    if bin_path is not None:
        logger.debug(
            "MCP Server '%s': 使用缓存的 %s → %s",
            config.name,
            package,
            bin_path,
        )
        return _rewrite_config(config, str(bin_path), extra_args)

    return None


async def resolve_npx_config(
    config: MCPServerConfig,
    *,
    cache_dir: Path | None = None,
) -> MCPServerConfig:
    """将 npx 命令解析为直接二进制调用（含安装）。

    - 非 ``npx`` 命令：原样返回。
    - 缓存命中：直接返回重写后的 config。
    - 缓存未命中：执行 ``npm install`` 后返回。
    - 任何失败：回退到原始 npx 命令（不阻断启动）。
    """
    if config.command != "npx":
        return config

    package, extra_args = _parse_npx_args(config.args)
    if not package:
        logger.warning(
            "MCP Server '%s': 无法从 npx 参数解析包名，保持原命令",
            config.name,
        )
        return config

    cache = cache_dir or _DEFAULT_CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)

    pkg_dir = cache / _safe_dir_name(package)
    bin_dir = pkg_dir / "node_modules" / ".bin"

    # 尝试从缓存中解析二进制
    bin_path = _find_bin(bin_dir, _bin_name_from_package(package))
    if bin_path is not None:
        logger.debug(
            "MCP Server '%s': 使用缓存的 %s → %s",
            config.name,
            package,
            bin_path,
        )
        return _rewrite_config(config, str(bin_path), extra_args)

    # 首次安装
    logger.info(
        "MCP Server '%s': 首次安装 %s 到缓存 %s ...",
        config.name,
        package,
        pkg_dir,
    )
    try:
        await _npm_install(package, pkg_dir, env=config.env)
    except Exception as exc:
        logger.warning(
            "MCP Server '%s': npm install %s 失败 (%s)，回退到 npx",
            config.name,
            package,
            exc,
        )
        return config

    # 安装后再查找二进制
    bin_path = _find_bin(bin_dir, _bin_name_from_package(package))
    if bin_path is None:
        logger.warning(
            "MCP Server '%s': 安装 %s 后未找到可执行文件，回退到 npx",
            config.name,
            package,
        )
        return config

    logger.info(
        "MCP Server '%s': 已缓存 %s → %s",
        config.name,
        package,
        bin_path,
    )
    return _rewrite_config(config, str(bin_path), extra_args)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

# npx 标志位：这些参数不是包名，需跳过
_NPX_FLAGS_NO_VALUE = frozenset({
    "-y", "--yes",
    "-q", "--quiet",
    "--no-install",
    "--prefer-online",
    "--prefer-offline",
    "--ignore-existing",
})
_NPX_FLAGS_WITH_VALUE = frozenset({
    "-p", "--package",
    "-c", "--call",
    "--shell",
    "--node-arg",
})


def _parse_npx_args(args: list[str]) -> tuple[str | None, list[str]]:
    """从 npx 参数中提取包名和剩余参数。

    Returns:
        (package_name, remaining_args)；无法解析时返回 (None, [])。
    """
    package_name: str | None = None
    remaining: list[str] = []
    skip_next = False

    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue

        if arg in _NPX_FLAGS_NO_VALUE:
            continue

        if arg in _NPX_FLAGS_WITH_VALUE:
            skip_next = True
            continue

        # 第一个非标志参数即为包名
        if package_name is None:
            package_name = arg
        else:
            remaining.append(arg)

    return package_name, remaining


def _bin_name_from_package(package: str) -> str:
    """从包名推导 bin 名称。

    @scope/foo-bar → foo-bar
    foo-bar → foo-bar
    """
    if "/" in package:
        return package.split("/", 1)[1]
    return package


def _safe_dir_name(package: str) -> str:
    """将包名转为安全目录名。@scope/name → scope__name"""
    return package.replace("@", "").replace("/", "__")


def _find_bin(bin_dir: Path, preferred_name: str) -> str | None:
    """在 node_modules/.bin 中查找可执行文件。

    优先匹配 preferred_name，否则返回第一个文件。
    未找到时返回 None。
    """
    if not bin_dir.is_dir():
        return None

    # 精确匹配
    candidate = bin_dir / preferred_name
    if candidate.exists():
        return str(candidate)

    # 回退：返回 .bin 下第一个非 .cmd/.ps1 文件
    for f in sorted(bin_dir.iterdir()):
        if f.suffix in (".cmd", ".ps1"):
            continue
        if f.is_file() or f.is_symlink():
            return str(f)

    return None


def _rewrite_config(
    config: MCPServerConfig,
    bin_path: str,
    extra_args: list[str],
) -> MCPServerConfig:
    """重写配置：npx → 直接二进制。"""
    return MCPServerConfig(
        name=config.name,
        transport=config.transport,
        command=bin_path,
        args=extra_args,
        env=config.env,
        timeout=config.timeout,
        auto_approve=config.auto_approve,
        state_dir=config.state_dir,
    )


async def _npm_install(package: str, prefix: Path, *, env: dict[str, str] | None = None) -> None:
    """运行 npm install 将包安装到指定目录。"""
    prefix.mkdir(parents=True, exist_ok=True)

    # 继承当前环境，合并 config.env
    run_env = dict(os.environ)
    if env:
        run_env.update(env)

    proc = await asyncio.create_subprocess_exec(
        "npm", "install", "--prefix", str(prefix), package,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=run_env,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        msg = stderr.decode(errors="replace").strip()[:500]
        raise RuntimeError(f"npm install 退出码 {proc.returncode}: {msg}")
