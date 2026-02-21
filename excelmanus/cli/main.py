"""CLI 主入口 — 初始化组件并启动 REPL。"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

from rich.console import Console

from excelmanus.cli.theme import THEME

logger = logging.getLogger(__name__)

# --save 启动参数
_AUTO_SAVE_PATH: str | None = None

# console 实例
console = Console()


async def _async_main() -> None:
    """异步入口：初始化组件并启动 REPL。"""
    from excelmanus import __version__
    from excelmanus.cli.commands import render_farewell
    from excelmanus.cli.repl import repl_loop
    from excelmanus.cli.welcome import render_welcome
    from excelmanus.config import ConfigError, load_config
    from excelmanus.engine import AgentEngine
    from excelmanus.logger import setup_logging
    from excelmanus.skillpacks import SkillpackLoader, SkillRouter
    from excelmanus.tools import ToolRegistry

    # ── 欢迎横幅（启动序列中打印进度） ──
    console.print()

    # ── 1. 加载配置 ──
    try:
        config = load_config()
    except ConfigError as exc:
        console.print(f"  [{THEME.RED}]{THEME.FAILURE} 配置错误：{exc}[/{THEME.RED}]")
        sys.exit(1)

    setup_logging(config.log_level)
    console.print(
        f"  [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS}[/{THEME.PRIMARY_LIGHT}]"
        f" [{THEME.DIM}]配置已加载[/{THEME.DIM}]",
        highlight=False,
    )

    # ── 2. 注册内置工具 ──
    registry = ToolRegistry()
    registry.register_builtin_tools(config.workspace_root)
    builtin_count = len(registry.get_tool_names())
    console.print(
        f"  [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS}[/{THEME.PRIMARY_LIGHT}]"
        f" [{THEME.DIM}]内置工具[/{THEME.DIM}]"
        f" [{THEME.BOLD} {THEME.PRIMARY_LIGHT}]{builtin_count}[/{THEME.BOLD} {THEME.PRIMARY_LIGHT}]",
        highlight=False,
    )

    # ── 3. 加载 Skillpacks ──
    loader = SkillpackLoader(config, registry)
    loader.load_all()
    router = SkillRouter(config, loader)
    skill_count = len(loader.list_skillpacks())
    console.print(
        f"  [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS}[/{THEME.PRIMARY_LIGHT}]"
        f" [{THEME.DIM}]Skillpacks[/{THEME.DIM}]"
        f" [{THEME.BOLD} {THEME.PRIMARY_LIGHT}]{skill_count}[/{THEME.BOLD} {THEME.PRIMARY_LIGHT}]",
        highlight=False,
    )

    # ── 4. 持久记忆 ──
    persistent_memory = None
    memory_extractor = None
    if config.memory_enabled:
        from excelmanus.memory_extractor import MemoryExtractor
        from excelmanus.persistent_memory import PersistentMemory
        from excelmanus.providers import create_client as _create_client

        persistent_memory = PersistentMemory(
            memory_dir=config.memory_dir,
            auto_load_lines=config.memory_auto_load_lines,
        )
        _client = _create_client(
            api_key=config.api_key,
            base_url=config.base_url,
        )
        memory_extractor = MemoryExtractor(client=_client, model=config.model)
        console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS}[/{THEME.PRIMARY_LIGHT}]"
            f" [{THEME.DIM}]持久记忆[/{THEME.DIM}]"
            f" [{THEME.BOLD} {THEME.PRIMARY_LIGHT}]已启用[/{THEME.BOLD} {THEME.PRIMARY_LIGHT}]",
            highlight=False,
        )
    else:
        console.print(
            f"  [{THEME.DIM}]○ 持久记忆已禁用[/{THEME.DIM}]",
            highlight=False,
        )

    # ── 5. 创建引擎 ──
    engine = AgentEngine(
        config,
        registry,
        skill_router=router,
        persistent_memory=persistent_memory,
        memory_extractor=memory_extractor,
    )

    # ── 6. MCP 连接 ──
    mcp_count = 0
    mcp_tool_count = 0
    with console.status(
        f"  [{THEME.DIM}]{THEME.AGENT_PREFIX} 正在连接 MCP Server…[/{THEME.DIM}]",
        spinner="dots",
        spinner_style=THEME.PRIMARY_LIGHT,
    ):
        t0 = time.monotonic()
        try:
            await engine.initialize_mcp()
        except Exception:
            logger.warning("MCP 初始化失败，已跳过", exc_info=True)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

    mcp_count = engine.mcp_connected_count
    if mcp_count > 0:
        for info in engine._mcp_manager.get_server_info():
            if info.get("status") == "ready":
                mcp_tool_count += info.get("tool_count", 0)
        console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS}[/{THEME.PRIMARY_LIGHT}]"
            f" [{THEME.DIM}]MCP Server[/{THEME.DIM}]"
            f" [{THEME.BOLD} {THEME.PRIMARY_LIGHT}]{mcp_count}[/{THEME.BOLD} {THEME.PRIMARY_LIGHT}]"
            f"  [{THEME.DIM}]({mcp_tool_count} 工具, {elapsed_ms}ms)[/{THEME.DIM}]",
            highlight=False,
        )
    else:
        console.print(
            f"  [{THEME.DIM}]○ 无 MCP Server ({elapsed_ms}ms)[/{THEME.DIM}]",
            highlight=False,
        )

    # ── 欢迎横幅 ──
    console.print()
    if _AUTO_SAVE_PATH is not None:
        save_hint = _AUTO_SAVE_PATH if _AUTO_SAVE_PATH else "outputs/conversations/ (自动)"
        console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS}[/{THEME.PRIMARY_LIGHT}]"
            f" [{THEME.DIM}]对话自动保存[/{THEME.DIM}]"
            f" [{THEME.BOLD} {THEME.PRIMARY_LIGHT}]{save_hint}[/{THEME.BOLD} {THEME.PRIMARY_LIGHT}]",
            highlight=False,
        )
    render_welcome(console, config, version=__version__, skill_count=skill_count, mcp_count=mcp_count)

    # ── REPL 期间抑制 INFO/DEBUG 日志输出到终端，避免噪音 ──
    _excelmanus_logger = logging.getLogger("excelmanus")
    _prev_handler_levels: list[tuple[logging.Handler, int]] = []
    for _h in _excelmanus_logger.handlers:
        if isinstance(_h, logging.StreamHandler):
            _prev_handler_levels.append((_h, _h.level))
            _h.setLevel(logging.WARNING)

    # ── 启动 REPL ──
    try:
        await repl_loop(console, engine)
    finally:
        # 恢复日志级别
        for _h, _lvl in _prev_handler_levels:
            _h.setLevel(_lvl)
        if _AUTO_SAVE_PATH is not None:
            try:
                from excelmanus.cli.repl import _handle_save_command
                save_input = f"/save {_AUTO_SAVE_PATH}".strip() if _AUTO_SAVE_PATH else "/save"
                _handle_save_command(console, engine, save_input)
            except Exception:
                logger.warning("CLI 退出时自动保存对话失败，已跳过", exc_info=True)
        try:
            await engine.extract_and_save_memory()
        except Exception:
            logger.warning("CLI 退出时持久记忆提取失败，已跳过", exc_info=True)
        try:
            await engine.shutdown_mcp()
        except Exception:
            logger.warning("CLI 退出时 MCP 关闭失败，已跳过", exc_info=True)


def main() -> None:
    """CLI 入口函数。"""
    import argparse

    from excelmanus.cli.commands import render_farewell

    parser = argparse.ArgumentParser(
        prog="excelmanus",
        description="ExcelManus — 基于大语言模型的 Excel 智能代理",
        add_help=False,
    )
    parser.add_argument(
        "--save",
        metavar="PATH",
        nargs="?",
        const="",
        default=None,
        help="退出时自动保存对话记录到 JSON（不指定路径则自动生成）",
    )
    args, _unknown = parser.parse_known_args()

    global _AUTO_SAVE_PATH
    _AUTO_SAVE_PATH = args.save

    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        render_farewell(console)
