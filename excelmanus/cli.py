"""CLI 交互模块：基于 Rich 的命令行对话界面。

提供 REPL 循环，支持自然语言指令、命令快捷键和优雅退出。
美化的欢迎面板、路由状态、工具调用卡片和执行摘要。
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
from contextlib import suppress
from typing import Callable

from rich.console import Console
from rich.cells import cell_len
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
from excelmanus.engine import AgentEngine, ChatResult
from excelmanus.events import EventType, ToolCallEvent
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
    "/subagent",
    "/sub_agent",
    "/fullaccess",
    "/full_access",
    "/accept",
    "/reject",
    "/undo",
}

_FULL_ACCESS_COMMAND_ALIASES = {"/fullaccess", "/full_access"}
_SUBAGENT_COMMAND_ALIASES = {"/subagent", "/sub_agent"}
_APPROVAL_COMMAND_ALIASES = {"/accept", "/reject", "/undo"}
_SESSION_CONTROL_COMMAND_ALIASES = (
    _FULL_ACCESS_COMMAND_ALIASES | _SUBAGENT_COMMAND_ALIASES | _APPROVAL_COMMAND_ALIASES
)

_SLASH_COMMAND_SUGGESTIONS = (
    "/help",
    "/history",
    "/clear",
    "/skills",
    "/subagent",
    "/sub_agent",
    "/fullAccess",
    "/full_access",
    "/fullaccess",
    "/accept",
    "/reject",
    "/undo",
)
_FULL_ACCESS_ARGUMENTS = ("status", "on", "off")
_SUBAGENT_ARGUMENTS = ("status", "on", "off", "list", "run")
_DYNAMIC_SKILL_SLASH_COMMANDS: tuple[str, ...] = ()


def _resolve_skill_slash_command(engine: AgentEngine, user_input: str) -> str | None:
    """识别是否为可手动调用的 Skill 斜杠命令。"""
    resolver = getattr(engine, "resolve_skill_command", None)
    if not callable(resolver):
        return None
    resolved = resolver(user_input)
    if isinstance(resolved, str) and resolved.strip():
        return resolved.strip()
    return None


def _extract_slash_raw_args(user_input: str) -> str:
    """提取 '/command ...' 中的参数字符串。"""
    if not user_input.startswith("/"):
        return ""
    _, _, raw_args = user_input[1:].partition(" ")
    return raw_args.strip()


def _parse_skills_payload_options(tokens: list[str], start_idx: int) -> dict:
    """解析 `--json` / `--json-file` 负载参数。"""
    json_text: str | None = None
    json_file: str | None = None
    idx = start_idx
    while idx < len(tokens):
        option = tokens[idx]
        if option == "--json":
            idx += 1
            if idx >= len(tokens):
                raise ValueError("`--json` 缺少参数。")
            if json_text is not None or json_file is not None:
                raise ValueError("`--json` 与 `--json-file` 只能二选一。")
            json_text = tokens[idx]
        elif option == "--json-file":
            idx += 1
            if idx >= len(tokens):
                raise ValueError("`--json-file` 缺少文件路径。")
            if json_text is not None or json_file is not None:
                raise ValueError("`--json` 与 `--json-file` 只能二选一。")
            json_file = tokens[idx]
        else:
            raise ValueError(f"未知参数：{option}")
        idx += 1

    if json_text is None and json_file is None:
        raise ValueError("缺少 payload，请使用 `--json` 或 `--json-file`。")

    if json_file is not None:
        with open(json_file, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    else:
        assert json_text is not None
        payload = json.loads(json_text)

    if not isinstance(payload, dict):
        raise ValueError("payload 必须为 JSON 对象。")
    return payload


def _handle_skills_subcommand(engine: AgentEngine, user_input: str) -> bool:
    """处理 `/skills ...` 子命令。返回是否已处理。"""
    if not user_input.startswith("/skills "):
        return False
    try:
        tokens = shlex.split(user_input)
    except ValueError as exc:
        console.print(f"  [red]✗ 命令解析失败：{exc}[/red]")
        return True

    if len(tokens) < 2:
        return False

    sub = tokens[1].lower()
    if sub == "list":
        rows = engine.list_skillpacks_detail()
        if not rows:
            console.print("  [dim]当前没有已加载的 Skillpack。[/dim]")
            return True
        table = Table(show_header=True, expand=False)
        table.add_column("name", style="magenta")
        table.add_column("source", style="cyan")
        table.add_column("writable", style="green")
        table.add_column("description")
        for row in rows:
            table.add_row(
                str(row.get("name", "")),
                str(row.get("source", "")),
                "yes" if bool(row.get("writable", False)) else "no",
                str(row.get("description", "")),
            )
        console.print()
        console.print(table)
        return True

    if sub == "get":
        if len(tokens) != 3:
            console.print("  [yellow]用法：/skills get <name>[/yellow]")
            return True
        name = tokens[2]
        detail = engine.get_skillpack_detail(name)
        console.print(
            json.dumps(detail, ensure_ascii=False, indent=2)
        )
        return True

    if sub == "create":
        if len(tokens) < 5:
            console.print(
                "  [yellow]用法：/skills create <name> --json '<payload>' "
                "或 --json-file <path>[/yellow]"
            )
            return True
        name = tokens[2]
        payload = _parse_skills_payload_options(tokens, 3)
        detail = engine.create_skillpack(name, payload, actor="cli")
        _sync_skill_command_suggestions(engine)
        console.print(
            json.dumps(
                {"status": "created", "name": detail.get("name"), "detail": detail},
                ensure_ascii=False,
                indent=2,
            )
        )
        return True

    if sub == "patch":
        if len(tokens) < 5:
            console.print(
                "  [yellow]用法：/skills patch <name> --json '<payload>' "
                "或 --json-file <path>[/yellow]"
            )
            return True
        name = tokens[2]
        payload = _parse_skills_payload_options(tokens, 3)
        detail = engine.patch_skillpack(name, payload, actor="cli")
        _sync_skill_command_suggestions(engine)
        console.print(
            json.dumps(
                {"status": "updated", "name": detail.get("name"), "detail": detail},
                ensure_ascii=False,
                indent=2,
            )
        )
        return True

    if sub == "delete":
        if len(tokens) < 3:
            console.print("  [yellow]用法：/skills delete <name> [--yes][/yellow]")
            return True
        name = tokens[2]
        flags = set(tokens[3:])
        if flags - {"--yes"}:
            console.print("  [yellow]仅支持参数：--yes[/yellow]")
            return True
        if "--yes" not in flags:
            console.print("  [yellow]删除需确认，请追加 `--yes`。[/yellow]")
            return True
        detail = engine.delete_skillpack(name, actor="cli", reason="cli_delete")
        _sync_skill_command_suggestions(engine)
        console.print(
            json.dumps(
                {"status": "deleted", "name": detail.get("name"), "detail": detail},
                ensure_ascii=False,
                indent=2,
            )
        )
        return True

    console.print(
        "  [yellow]未知 /skills 子命令。可用：list/get/create/patch/delete[/yellow]"
    )
    return True


def _reply_text(result: ChatResult | str) -> str:
    """兼容 chat() 新旧返回类型，统一提取展示文本。"""
    if isinstance(result, ChatResult):
        return result.reply
    return str(result)


def _load_skill_command_rows(engine: AgentEngine) -> list[tuple[str, str]]:
    """读取技能命令列表，格式为 [(name, argument_hint), ...]。"""
    list_commands = getattr(engine, "list_skillpack_commands", None)
    if callable(list_commands):
        rows = list_commands()
        normalized: list[tuple[str, str]] = []
        for row in rows:
            if (
                isinstance(row, tuple)
                and len(row) == 2
                and isinstance(row[0], str)
                and isinstance(row[1], str)
            ):
                normalized.append((row[0], row[1]))
        return normalized

    list_loaded = getattr(engine, "list_loaded_skillpacks", None)
    if callable(list_loaded):
        names = list_loaded()
        return [
            (name, "")
            for name in names
            if isinstance(name, str) and name.strip()
        ]
    return []


def _sync_skill_command_suggestions(engine: AgentEngine) -> None:
    """将已加载 Skillpack 更新到斜杠命令补全缓存。"""
    global _DYNAMIC_SKILL_SLASH_COMMANDS
    rows = _load_skill_command_rows(engine)
    _DYNAMIC_SKILL_SLASH_COMMANDS = tuple(f"/{name}" for name, _ in rows)


def _list_known_slash_commands() -> tuple[str, ...]:
    ordered = list(_SLASH_COMMAND_SUGGESTIONS)
    ordered.extend(_DYNAMIC_SKILL_SLASH_COMMANDS)
    # 保序去重
    return tuple(dict.fromkeys(ordered))


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
        for suggestion in _list_known_slash_commands():
            if suggestion.lower() == lowered_command:
                return None
            if suggestion.lower().startswith(lowered_command):
                return suggestion[len(user_input) :]
        return None

    # 再补全控制命令参数：如 /fullAccess s -> /fullAccess status
    command_arguments = {
        alias: _FULL_ACCESS_ARGUMENTS for alias in _FULL_ACCESS_COMMAND_ALIASES
    }
    command_arguments.update(
        {alias: _SUBAGENT_ARGUMENTS for alias in _SUBAGENT_COMMAND_ALIASES}
    )
    available_arguments = command_arguments.get(lowered_command)
    if available_arguments is None:
        return None

    current_arg = remainder.strip()
    if not current_arg:
        return available_arguments[0]
    if " " in current_arg:
        return None

    lowered_arg = current_arg.lower()
    for candidate in available_arguments:
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
    info.append("  子代理  ", style="dim")
    info.append(
        ("已启用" if config.subagent_enabled else "已禁用") + "\n",
        style="bold cyan" if config.subagent_enabled else "bold red",
    )
    info.append("  目录  ", style="dim")
    info.append(f"{os.path.abspath(config.workspace_root)}\n\n", style="")

    # 快捷命令
    info.append("  命令  ", style="dim")
    info.append("/help", style="green")
    info.append("  /history", style="green")
    info.append("  /clear", style="green")
    info.append("  /skills", style="green")
    info.append("  /subagent", style="green")
    info.append("  /fullAccess", style="green")
    info.append("  /accept <id>", style="green")
    info.append("  /reject <id>", style="green")
    info.append("  /undo <id>", style="green")
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


async def _read_multiline_user_input() -> str:
    """读取多行输入：空行提交，返回换行拼接后的文本。"""
    lines: list[str] = []
    while True:
        line = await _read_user_input()
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines)


def _render_help(engine: AgentEngine | None = None) -> None:
    """渲染帮助信息。"""
    table = Table(show_header=False, show_edge=False, pad_edge=False, expand=False)
    table.add_column("命令", style="green", min_width=14)
    table.add_column("说明")

    table.add_row("/help", "显示此帮助信息")
    table.add_row("/history", "显示当前会话的对话历史摘要")
    table.add_row("/clear", "清除当前对话历史")
    table.add_row("/skills", "查看已加载 Skillpacks 与本轮路由结果")
    table.add_row("/skills list", "列出全部 Skillpack 摘要")
    table.add_row("/skills get <name>", "查看单个 Skillpack 详情")
    table.add_row("/skills create <name> --json/--json-file", "创建 project Skillpack")
    table.add_row("/skills patch <name> --json/--json-file", "更新 project Skillpack")
    table.add_row("/skills delete <name> [--yes]", "软删除 project Skillpack")
    table.add_row("/subagent [on|off|status|list]", "会话级 subagent 开关与列表")
    table.add_row("/subagent run -- <task>", "自动选择 subagent 执行任务")
    table.add_row("/subagent run <agent> -- <task>", "指定 subagent 执行任务")
    table.add_row("/fullAccess [on|off|status]", "会话级代码技能权限控制")
    table.add_row("/accept <id>", "执行待确认高风险操作")
    table.add_row("/reject <id>", "拒绝待确认高风险操作")
    table.add_row("/undo <id>", "回滚已确认且可回滚的操作")
    table.add_row("/<skill_name> [args...]", "手动调用指定 Skillpack（如 /data_basic）")
    table.add_row("多选回答", "待回答问题为多选时：每行一个选项，空行提交")
    skill_rows = _load_skill_command_rows(engine) if engine is not None else []
    for name, argument_hint in skill_rows:
        hint_text = argument_hint if argument_hint else "(无参数提示)"
        table.add_row(f"/{name}", f"Skillpack 参数：{hint_text}")
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
    table.add_row(
        "子代理状态",
        "enabled" if engine.subagent_enabled else "disabled",
    )

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


def _is_interactive_terminal() -> bool:
    """判断当前是否交互式终端。"""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


class _LiveStatusTicker:
    """CLI 动态状态提示：在等待回复期间输出灰色滚动文本。"""

    _FRAMES = ("...", "..", ".")

    def __init__(self, console: Console, *, enabled: bool, interval: float = 0.3) -> None:
        self._console = console
        self._enabled = enabled
        self._interval = interval
        self._status_label = "思考中"
        self._frame_index = 0
        self._task: asyncio.Task[None] | None = None
        self._last_line_width = 0

    async def start(self) -> None:
        """启动动态提示。"""
        if not self._enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """停止动态提示并清理状态行。"""
        task = self._task
        self._task = None

        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        if self._enabled:
            self._clear_line()

    def wrap_handler(
        self,
        handler: Callable[[ToolCallEvent], None],
    ) -> Callable[[ToolCallEvent], None]:
        """包装事件回调：先更新状态提示，再执行原渲染逻辑。"""
        if not self._enabled:
            return handler

        def _wrapped(event: ToolCallEvent) -> None:
            self._clear_line()
            self._update_state_from_event(event)
            handler(event)

        return _wrapped

    async def _run(self) -> None:
        while True:
            suffix = self._FRAMES[self._frame_index % len(self._FRAMES)]
            self._frame_index += 1
            line = f"{self._status_label}{suffix}"
            line_width = cell_len(line)
            self._last_line_width = max(self._last_line_width, line_width)
            padding = " " * max(self._last_line_width - line_width, 0)
            self._console.print(Text(f"{line}{padding}", style="dim"), end="\r")
            await asyncio.sleep(self._interval)

    def _update_state_from_event(self, event: ToolCallEvent) -> None:
        if event.event_type == EventType.TOOL_CALL_START:
            tool_name = event.tool_name.strip()
            self._status_label = (
                f"调用工具 {tool_name}" if tool_name else "调用工具"
            )
            return
        if event.event_type in (EventType.SUBAGENT_START, EventType.SUBAGENT_SUMMARY):
            self._status_label = "调用子代理"
            return
        if event.event_type == EventType.CHAT_SUMMARY:
            self._status_label = "整理结果"
            return
        # 默认回到思考态
        self._status_label = "思考中"

    def _clear_line(self) -> None:
        if self._last_line_width <= 0:
            return
        self._console.print(" " * self._last_line_width, end="\r")


async def _chat_with_feedback(
    engine: AgentEngine,
    *,
    user_input: str,
    renderer: StreamRenderer,
    slash_command: str | None = None,
    raw_args: str | None = None,
) -> str:
    """统一封装 chat 调用，增加等待期动态状态反馈。"""
    ticker = _LiveStatusTicker(console, enabled=_is_interactive_terminal())
    event_handler = ticker.wrap_handler(renderer.handle_event)

    await ticker.start()
    try:
        chat_kwargs: dict[str, object] = {"on_event": event_handler}
        if slash_command is not None:
            chat_kwargs["slash_command"] = slash_command
        if raw_args is not None:
            chat_kwargs["raw_args"] = raw_args
        return _reply_text(await engine.chat(user_input, **chat_kwargs))
    finally:
        await ticker.stop()


async def _repl_loop(engine: AgentEngine) -> None:
    """异步 REPL 主循环。"""
    _sync_skill_command_suggestions(engine)
    while True:
        has_pending_question = bool(
            getattr(engine, "has_pending_question", lambda: False)()
        )
        waiting_multiselect = bool(
            getattr(engine, "is_waiting_multiselect_answer", lambda: False)()
        )
        try:
            if waiting_multiselect:
                console.print(
                    "  [dim]多选回答模式：每行输入一个选项，空行提交。[/dim]"
                )
                user_input = (await _read_multiline_user_input()).strip()
            else:
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

        if has_pending_question:
            try:
                renderer = StreamRenderer(console)
                console.print()
                reply = await _chat_with_feedback(
                    engine,
                    user_input=user_input,
                    renderer=renderer,
                )
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
                logger.error("处理待回答问题时发生错误: %s", exc, exc_info=True)
                console.print(f"  [red]✗ 处理请求时发生错误：{exc}[/red]")
            continue

        # 斜杠命令处理
        if user_input.lower() == "/help":
            _render_help(engine)
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

        if user_input.startswith("/skills "):
            try:
                handled = _handle_skills_subcommand(engine, user_input)
            except Exception as exc:  # noqa: BLE001
                logger.error("处理 /skills 子命令失败: %s", exc, exc_info=True)
                console.print(f"  [red]✗ /skills 子命令执行失败：{exc}[/red]")
                handled = True
            if handled:
                continue

        # 会话控制命令统一走 engine.chat（与 API 行为一致）
        lowered_parts = user_input.lower().split()
        lowered_cmd = lowered_parts[0] if lowered_parts else ""
        if lowered_cmd in _SESSION_CONTROL_COMMAND_ALIASES:
            reply = _reply_text(await engine.chat(user_input))
            console.print(f"  [cyan]{reply}[/cyan]")
            continue

        # Skill 斜杠命令：如 /data_basic ...（走手动 Skill 路由）
        resolved_skill = (
            _resolve_skill_slash_command(engine, user_input)
            if user_input.startswith("/")
            else None
        )
        if resolved_skill:
            raw_args = _extract_slash_raw_args(user_input)
            argument_hint_getter = getattr(engine, "get_skillpack_argument_hint", None)
            argument_hint = (
                argument_hint_getter(resolved_skill)
                if callable(argument_hint_getter)
                else ""
            )
            if not raw_args and isinstance(argument_hint, str) and argument_hint.strip():
                console.print(f"  [yellow]参数提示：{argument_hint.strip()}[/yellow]")
            try:
                renderer = StreamRenderer(console)
                console.print()
                reply = await _chat_with_feedback(
                    engine,
                    user_input=user_input,
                    renderer=renderer,
                    slash_command=resolved_skill,
                    raw_args=raw_args,
                )

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
            known_commands = _list_known_slash_commands()
            suggestion = ", ".join(known_commands[:8]) if known_commands else "/help"
            console.print(
                f"  [yellow]未知命令：{user_input}。可用命令示例：{suggestion}[/yellow]"
            )
            continue

        # 自然语言指令：调用 AgentEngine，使用事件流渲染
        try:
            renderer = StreamRenderer(console)
            console.print()  # 空行分隔
            reply = await _chat_with_feedback(
                engine,
                user_input=user_input,
                renderer=renderer,
            )

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

    # 根据 memory_enabled 创建持久记忆组件
    persistent_memory = None
    memory_extractor = None
    if config.memory_enabled:
        from excelmanus.persistent_memory import PersistentMemory
        from excelmanus.memory_extractor import MemoryExtractor

        import openai as _openai

        persistent_memory = PersistentMemory(
            memory_dir=config.memory_dir,
            auto_load_lines=config.memory_auto_load_lines,
        )
        _client = _openai.AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )
        memory_extractor = MemoryExtractor(client=_client, model=config.model)

    # 创建 AgentEngine
    engine = AgentEngine(
        config,
        registry,
        skill_router=router,
        persistent_memory=persistent_memory,
        memory_extractor=memory_extractor,
    )
    _sync_skill_command_suggestions(engine)

    # 渲染欢迎信息
    skill_count = len(engine.list_loaded_skillpacks())
    _render_welcome(config, skill_count)

    # 启动 REPL 循环
    try:
        await _repl_loop(engine)
    finally:
        try:
            await engine.extract_and_save_memory()
        except Exception:
            logger.warning("CLI 退出时持久记忆提取失败，已跳过", exc_info=True)


def main() -> None:
    """CLI 入口函数。"""
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        # 顶层捕获 Ctrl+C，确保优雅退出
        _render_farewell()
