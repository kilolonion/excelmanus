"""CLI 交互模块：基于 Rich 的命令行对话界面。

提供 REPL 循环，支持自然语言指令、命令快捷键和优雅退出。
"""

from __future__ import annotations

import asyncio
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from excelmanus import __version__
from excelmanus.config import ConfigError, load_config
from excelmanus.engine import AgentEngine
from excelmanus.logger import get_logger, setup_logging
from excelmanus.renderer import StreamRenderer
from excelmanus.skills import SkillRegistry

logger = get_logger("cli")

# Rich 控制台实例
console = Console()

# 退出命令集合
_EXIT_COMMANDS = {"exit", "quit"}

# 斜杠命令集合
_SLASH_COMMANDS = {"/help", "/history", "/clear"}


def _render_welcome() -> None:
    """渲染欢迎信息面板。"""
    welcome_text = Text()
    welcome_text.append("ExcelManus", style="bold cyan")
    welcome_text.append(f" v{__version__}\n", style="dim")
    welcome_text.append("基于大语言模型的 Excel 智能代理\n\n", style="")
    welcome_text.append("可用命令：\n", style="bold")
    welcome_text.append("  /help    ", style="green")
    welcome_text.append("显示帮助信息\n")
    welcome_text.append("  /history ", style="green")
    welcome_text.append("查看对话历史\n")
    welcome_text.append("  /clear   ", style="green")
    welcome_text.append("清除对话历史\n")
    welcome_text.append("  exit     ", style="green")
    welcome_text.append("退出程序\n")

    console.print(Panel(welcome_text, title="欢迎", border_style="cyan"))


def _render_help() -> None:
    """渲染帮助信息。"""
    help_text = Text()
    help_text.append("命令列表：\n\n", style="bold")
    help_text.append("  /help       ", style="green")
    help_text.append("显示此帮助信息\n")
    help_text.append("  /history    ", style="green")
    help_text.append("显示当前会话的对话历史摘要\n")
    help_text.append("  /clear      ", style="green")
    help_text.append("清除当前对话历史\n")
    help_text.append("  exit/quit   ", style="green")
    help_text.append("退出程序\n")
    help_text.append("  Ctrl+C      ", style="green")
    help_text.append("退出程序\n\n")
    help_text.append("直接输入自然语言即可与 Excel 智能代理对话。", style="dim")

    console.print(Panel(help_text, title="帮助", border_style="blue"))


def _render_history(engine: AgentEngine) -> None:
    """渲染对话历史摘要。"""
    messages = engine.memory.get_messages()

    # 过滤掉 system 消息，只展示用户和助手的对话
    history_entries: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "user" and content:
            # 截断过长的用户输入
            display = content if len(content) <= 80 else content[:77] + "..."
            history_entries.append(f"👤 用户：{display}")
        elif role == "assistant" and content:
            display = content if len(content) <= 80 else content[:77] + "..."
            history_entries.append(f"🤖 助手：{display}")

    if not history_entries:
        console.print("[dim]暂无对话历史。[/dim]")
        return

    console.print(
        Panel(
            "\n".join(history_entries),
            title=f"对话历史（共 {len(history_entries)} 条）",
            border_style="yellow",
        )
    )


def _render_farewell() -> None:
    """渲染告别信息。"""
    console.print("\n[cyan]感谢使用 ExcelManus，再见！👋[/cyan]")


async def _repl_loop(engine: AgentEngine) -> None:
    """异步 REPL 主循环。"""
    while True:
        try:
            user_input = console.input("[bold green]>>> [/bold green]").strip()
        except (KeyboardInterrupt, EOFError):
            # Ctrl+C 或 Ctrl+D 优雅退出
            _render_farewell()
            return

        # 空输入跳过
        if not user_input:
            continue

        # 退出命令
        if user_input.lower() in _EXIT_COMMANDS:
            _render_farewell()
            return

        # 斜杠命令处理
        if user_input.lower() == "/help":
            _render_help()
            continue

        if user_input.lower() == "/history":
            _render_history(engine)
            continue

        if user_input.lower() == "/clear":
            engine.clear_memory()
            console.print("[green]✓ 对话历史已清除。[/green]")
            continue

        # 未知斜杠命令提示
        if user_input.startswith("/"):
            console.print(
                f"[yellow]未知命令：{user_input}。输入 /help 查看可用命令。[/yellow]"
            )
            continue

        # 自然语言指令：调用 AgentEngine，使用事件流渲染替代 spinner
        try:
            renderer = StreamRenderer(console)
            reply = await engine.chat(user_input, on_event=renderer.handle_event)

            # 使用 Rich Markdown 渲染输出
            console.print()
            console.print(Markdown(reply))
            console.print()

        except KeyboardInterrupt:
            _render_farewell()
            return
        except Exception as exc:
            logger.error("处理请求时发生错误: %s", exc, exc_info=True)
            console.print(f"[red]处理请求时发生错误：{exc}[/red]")


async def _async_main() -> None:
    """异步入口：初始化组件并启动 REPL。"""
    # 加载配置
    try:
        config = load_config()
    except ConfigError as exc:
        console.print(f"[red]配置错误：{exc}[/red]")
        sys.exit(1)

    # 配置日志
    setup_logging(config.log_level)

    # 初始化 Skill 注册中心
    registry = SkillRegistry()
    registry.auto_discover()

    # 创建 AgentEngine
    engine = AgentEngine(config, registry)

    # 渲染欢迎信息
    _render_welcome()

    # 启动 REPL 循环
    await _repl_loop(engine)


def main() -> None:
    """CLI 入口函数。"""
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        # 顶层捕获 Ctrl+C，确保优雅退出
        _render_farewell()
