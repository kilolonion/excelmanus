"""CLI 交互模块：基于 Rich 的命令行对话界面。

提供 REPL 循环，支持自然语言指令、命令快捷键和优雅退出。
美化的欢迎面板、路由状态、工具调用卡片和执行摘要。
"""

from __future__ import annotations

import asyncio
import os
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style

    _PROMPT_TOOLKIT_ENABLED = True
except ImportError:  # pragma: no cover - 依赖缺失时走 Rich 输入回退
    _PROMPT_TOOLKIT_ENABLED = False

from excelmanus import __version__
from excelmanus.config import ConfigError, load_config
from excelmanus.engine import AgentEngine
from excelmanus.logger import get_logger, setup_logging
from excelmanus.renderer import StreamRenderer
from excelmanus.skillpacks import SkillpackLoader, SkillRouter
from excelmanus.tools import ToolRegistry

logger = get_logger("cli")

# Rich 控制台实例
console = Console()

# 退出命令集合
_EXIT_COMMANDS = {"exit", "quit"}

# 斜杠命令集合
_SLASH_COMMANDS = {
    "/help",
    "/history",
    "/clear",
    "/skills",
    "/fullaccess",
    "/full_access",
}

_FULL_ACCESS_COMMAND_ALIASES = {"/fullaccess", "/full_access"}

_SLASH_COMMAND_SUGGESTIONS = (
    "/help",
    "/history",
    "/clear",
    "/skills",
    "/fullAccess",
    "/full_access",
    "/fullaccess",
)
_FULL_ACCESS_ARGUMENTS = ("status", "on", "off")


def _resolve_skill_slash_command(engine: AgentEngine, user_input: str) -> str | None:
    """识别是否为可手动调用的 Skill 斜杠命令。"""
    resolver = getattr(engine, "resolve_skill_command", None)
    if not callable(resolver):
        return None
    resolved = resolver(user_input)
    if isinstance(resolved, str) and resolved.strip():
        return resolved.strip()
    return None

# ASCII Logo
_LOGO = r"""
  ______               _ __  __
 |  ____|             | |  \/  |
 | |__  __  _____ ___ | | \  / | __ _ _ __  _   _ ___
 |  __| \ \/ / __/ _ \| | |\/| |/ _` | '_ \| | | / __|
 | |____ >  < (_|  __/| | |  | | (_| | | | | |_| \__ \
 |______/_/\_\___\___||_|_|  |_|\__,_|_| |_|\__,_|___/
"""


def _compute_inline_suggestion(user_input: str) -> str | None:
    """根据当前输入计算可追加的补全文本（返回后缀）。"""
    if not user_input.startswith("/"):
        return None

    command, separator, remainder = user_input.partition(" ")
    lowered_command = command.lower()

    # 先补全命令本体：如 /ful -> /fullAccess
    if not separator:
        for suggestion in _SLASH_COMMAND_SUGGESTIONS:
            if suggestion.lower() == lowered_command:
                return None
            if suggestion.lower().startswith(lowered_command):
                return suggestion[len(user_input) :]
        return None

    # 再补全 fullAccess 的参数：如 /fullAccess s -> /fullAccess status
    if lowered_command not in _FULL_ACCESS_COMMAND_ALIASES:
        return None

    current_arg = remainder.strip()
    if not current_arg:
        return _FULL_ACCESS_ARGUMENTS[0]
    if " " in current_arg:
        return None

    lowered_arg = current_arg.lower()
    for candidate in _FULL_ACCESS_ARGUMENTS:
        if candidate == lowered_arg:
            return None
        if candidate.startswith(lowered_arg):
            return candidate[len(current_arg) :]
    return None


if _PROMPT_TOOLKIT_ENABLED:

    class _SlashCommandAutoSuggest(AutoSuggest):
        """基于斜杠命令的内联补全建议器。"""

        def get_suggestion(self, buffer, document):  # type: ignore[override]
            suffix = _compute_inline_suggestion(document.text_before_cursor)
            if suffix is None:
                return None
            return Suggestion(suffix)


    _PROMPT_HISTORY = InMemoryHistory()
    _PROMPT_STYLE = Style.from_dict({"auto-suggestion": "ansibrightblack"})
    _SLASH_AUTO_SUGGEST = _SlashCommandAutoSuggest()
    _PROMPT_KEY_BINDINGS = KeyBindings()

    @_PROMPT_KEY_BINDINGS.add("tab")
    def _accept_inline_suggestion(event) -> None:
        """按 Tab 接受灰色补全建议。"""
        suggestion = event.current_buffer.suggestion
        if suggestion:
            event.current_buffer.insert_text(suggestion.text)


def _render_welcome(config: "ExcelManusConfig", skill_count: int) -> None:
    """渲染欢迎信息面板 — 含 Logo、版本、模型、技能包信息。"""
    from excelmanus.config import ExcelManusConfig  # noqa: F811 避免循环导入

    # 构建信息区
    info = Text()
    info.append(_LOGO, style="bold cyan")
    info.append(f"\n  v{__version__}", style="bold white")
    info.append("  ·  基于大语言模型的 Excel 智能代理\n\n", style="dim")

    # 环境信息
    model_display = config.model
    info.append("  模型  ", style="dim")
    info.append(f"{model_display}\n", style="bold yellow")
    info.append("  技能  ", style="dim")
    info.append(f"{skill_count} 个 Skillpack 已加载\n", style="bold green")
    info.append("  目录  ", style="dim")
    info.append(f"{os.path.abspath(config.workspace_root)}\n\n", style="")

    # 快捷命令
    info.append("  命令  ", style="dim")
    info.append("/help", style="green")
    info.append("  /history", style="green")
    info.append("  /clear", style="green")
    info.append("  /skills", style="green")
    info.append("  /fullAccess", style="green")
    info.append("  /<skill_name>", style="green")
    info.append("  exit\n", style="green")

    console.print(
        Panel(
            info,
            border_style="cyan",
            padding=(0, 1),
        )
    )


_PROMPT_SESSION = None
if _PROMPT_TOOLKIT_ENABLED:
    _PROMPT_SESSION = PromptSession(
        history=_PROMPT_HISTORY,
        auto_suggest=_SLASH_AUTO_SUGGEST,
        style=_PROMPT_STYLE,
        key_bindings=_PROMPT_KEY_BINDINGS,
    )


async def _read_user_input() -> str:
    """读取用户输入：优先使用 prompt_toolkit 的异步输入能力。"""
    if (
        _PROMPT_TOOLKIT_ENABLED
        and _PROMPT_SESSION is not None
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    ):
        try:
            return await _PROMPT_SESSION.prompt_async(ANSI("\n \x1b[1;32m❯\x1b[0m "))
        except (KeyboardInterrupt, EOFError):
            raise
        except Exception as exc:  # pragma: no cover - 仅保护交互式边界
            logger.warning("prompt_toolkit 输入失败，回退到基础输入：%s", exc)

    return console.input("\n [bold green]❯[/bold green] ")


def _render_help() -> None:
    """渲染帮助信息。"""
    table = Table(show_header=False, show_edge=False, pad_edge=False, expand=False)
    table.add_column("命令", style="green", min_width=14)
    table.add_column("说明")

    table.add_row("/help", "显示此帮助信息")
    table.add_row("/history", "显示当前会话的对话历史摘要")
    table.add_row("/clear", "清除当前对话历史")
    table.add_row("/skills", "查看已加载 Skillpacks 与本轮路由结果")
    table.add_row("/fullAccess [on|off|status]", "会话级代码技能权限控制")
    table.add_row("/<skill_name> [args...]", "手动调用指定 Skillpack（如 /data_basic）")
    table.add_row("exit / quit", "退出程序")
    table.add_row("Ctrl+C", "退出程序")

    console.print()
    console.print(
        Panel(
            table,
            title="[bold]帮助[/bold]",
            title_align="left",
            border_style="blue",
            expand=False,
            padding=(1, 2),
            subtitle="[dim]直接输入自然语言即可与代理对话[/dim]",
            subtitle_align="left",
        )
    )
    console.print()


def _render_history(engine: AgentEngine) -> None:
    """渲染对话历史摘要。"""
    messages = engine.memory.get_messages()

    # 过滤掉 system 消息，只展示用户和助手的对话
    history_entries: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "user" and content:
            display = content if len(content) <= 80 else content[:77] + "…"
            history_entries.append(f"  [bold green]▸[/bold green] {display}")
        elif role == "assistant" and content:
            display = content if len(content) <= 80 else content[:77] + "…"
            history_entries.append(f"  [bold cyan]◂[/bold cyan] {display}")

    if not history_entries:
        console.print("  [dim]暂无对话历史。[/dim]")
        return

    console.print()
    console.print(
        Panel(
            "\n".join(history_entries),
            title=f"[bold]对话历史[/bold] [dim]({len(history_entries)} 条)[/dim]",
            title_align="left",
            border_style="yellow",
            expand=False,
            padding=(1, 1),
        )
    )
    console.print()


def _render_farewell() -> None:
    """渲染告别信息。"""
    console.print("\n  [cyan]感谢使用 ExcelManus，再见！[/cyan] 👋\n")


def _render_skills(engine: AgentEngine) -> None:
    """渲染已加载 Skillpack 与最近一次路由结果。"""
    loaded = engine.list_loaded_skillpacks()
    route = engine.last_route_result

    table = Table(show_header=False, show_edge=False, pad_edge=False, expand=False)
    table.add_column(style="dim", min_width=12)
    table.add_column()

    table.add_row(
        "已加载",
        ", ".join(f"[magenta]{s}[/magenta]" for s in loaded) if loaded else "[dim]无[/dim]",
    )
    table.add_row("路由模式", f"[yellow]{route.route_mode}[/yellow]")
    table.add_row(
        "命中技能",
        ", ".join(f"[bold]{s}[/bold]" for s in route.skills_used)
        if route.skills_used
        else "[dim]无[/dim]",
    )
    tool_count = len(route.tool_scope) if route.tool_scope else 0
    table.add_row("工具范围", f"{tool_count} 个工具")
    permission = "full_access" if engine.full_access_enabled else "restricted"
    table.add_row("代码技能权限", permission)

    console.print()
    console.print(
        Panel(
            table,
            title="[bold]🧩 Skillpacks[/bold]",
            title_align="left",
            border_style="magenta",
            expand=False,
            padding=(0, 2),
        )
    )
    console.print()


async def _repl_loop(engine: AgentEngine) -> None:
    """异步 REPL 主循环。"""
    while True:
        try:
            user_input = (await _read_user_input()).strip()
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
            console.print("  [green]✓ 对话历史已清除。[/green]")
            continue

        if user_input.lower() == "/skills":
            _render_skills(engine)
            continue

        # fullAccess 控制命令统一走 engine.chat（与 API 行为一致）
        lowered_parts = user_input.lower().split()
        lowered_cmd = lowered_parts[0] if lowered_parts else ""
        if lowered_cmd in _FULL_ACCESS_COMMAND_ALIASES:
            reply = await engine.chat(user_input)
            console.print(f"  [cyan]{reply}[/cyan]")
            continue

        # Skill 斜杠命令：如 /data_basic ...（走手动 Skill 路由）
        if user_input.startswith("/") and _resolve_skill_slash_command(engine, user_input):
            try:
                renderer = StreamRenderer(console)
                console.print()
                reply = await engine.chat(user_input, on_event=renderer.handle_event)

                console.print()
                console.print(
                    Panel(
                        Markdown(reply),
                        border_style="dim cyan",
                        padding=(1, 2),
                        expand=False,
                    )
                )
            except KeyboardInterrupt:
                _render_farewell()
                return
            except Exception as exc:
                logger.error("处理请求时发生错误: %s", exc, exc_info=True)
                console.print(f"  [red]✗ 处理请求时发生错误：{exc}[/red]")
            continue

        # 未知斜杠命令提示
        if user_input.startswith("/"):
            console.print(
                f"  [yellow]未知命令：{user_input}。输入 /help 查看可用命令。[/yellow]"
            )
            continue

        # 自然语言指令：调用 AgentEngine，使用事件流渲染
        try:
            renderer = StreamRenderer(console)
            console.print()  # 空行分隔
            reply = await engine.chat(user_input, on_event=renderer.handle_event)

            # 使用 Rich Markdown 渲染最终回复
            console.print()
            console.print(
                Panel(
                    Markdown(reply),
                    border_style="dim cyan",
                    padding=(1, 2),
                    expand=False,
                )
            )

        except KeyboardInterrupt:
            _render_farewell()
            return
        except Exception as exc:
            logger.error("处理请求时发生错误: %s", exc, exc_info=True)
            console.print(f"  [red]✗ 处理请求时发生错误：{exc}[/red]")


async def _async_main() -> None:
    """异步入口：初始化组件并启动 REPL。"""
    # 加载配置
    try:
        config = load_config()
    except ConfigError as exc:
        console.print(f"  [red]✗ 配置错误：{exc}[/red]")
        sys.exit(1)

    # 配置日志
    setup_logging(config.log_level)

    # 初始化 ToolRegistry
    registry = ToolRegistry()
    registry.register_builtin_tools(config.workspace_root)

    # 初始化 Skillpack 路由
    loader = SkillpackLoader(config, registry)
    loader.load_all()
    router = SkillRouter(config, loader)

    # 创建 AgentEngine
    engine = AgentEngine(config, registry, skill_router=router)

    # 渲染欢迎信息
    skill_count = len(engine.list_loaded_skillpacks())
    _render_welcome(config, skill_count)

    # 启动 REPL 循环
    await _repl_loop(engine)


def main() -> None:
    """CLI 入口函数。"""
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        # 顶层捕获 Ctrl+C，确保优雅退出
        _render_farewell()
