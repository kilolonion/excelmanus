"""CLI 交互模块：基于 Rich 的命令行对话界面。

提供 REPL 循环，支持自然语言指令、命令快捷键和优雅退出。
美化的欢迎面板、路由状态、工具调用卡片和执行摘要。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import sys
from contextlib import suppress
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.cells import cell_len
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.application import Application
    from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
    from prompt_toolkit.formatted_text import ANSI, FormattedText
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout
    from prompt_toolkit.styles import Style

    _PROMPT_TOOLKIT_ENABLED = True
except ImportError:  # pragma: no cover - 依赖缺失时走 Rich 输入回退
    _PROMPT_TOOLKIT_ENABLED = False

from excelmanus import __version__
from excelmanus.config import ConfigError, load_config
from excelmanus.engine import AgentEngine, ChatResult
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.question_flow import PendingQuestion
from excelmanus.approval import PendingApproval
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
    "/plan",
    "/model",
    "/config",
}

_FULL_ACCESS_COMMAND_ALIASES = {"/fullaccess", "/full_access"}
_SUBAGENT_COMMAND_ALIASES = {"/subagent", "/sub_agent"}
_APPROVAL_COMMAND_ALIASES = {"/accept", "/reject", "/undo"}
_PLAN_COMMAND_ALIASES = {"/plan"}
_MODEL_COMMAND_ALIASES = {"/model"}
_CONFIG_COMMAND_ALIASES = {"/config"}
_SESSION_CONTROL_COMMAND_ALIASES = (
    _FULL_ACCESS_COMMAND_ALIASES
    | _SUBAGENT_COMMAND_ALIASES
    | _APPROVAL_COMMAND_ALIASES
    | _PLAN_COMMAND_ALIASES
    | _MODEL_COMMAND_ALIASES
)

_SLASH_COMMAND_SUGGESTIONS = (
    "/help",
    "/history",
    "/clear",
    "/skills",
    "/subagent",
    "/sub_agent",
    "/mcp",
    "/config",
    "/fullAccess",
    "/full_access",
    "/fullaccess",
    "/accept",
    "/reject",
    "/undo",
    "/plan",
    "/model",
)
_CONFIG_ARGUMENTS = ("list", "set", "get", "delete")
_FULL_ACCESS_ARGUMENTS = ("status", "on", "off")
_SUBAGENT_ARGUMENTS = ("status", "on", "off", "list", "run")
_PLAN_ARGUMENTS = ("status", "on", "off", "approve", "reject")
_MODEL_ARGUMENTS: tuple[str, ...] = ("list",)  # 动态模型名称在运行时追加
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


def _to_standard_skill_detail(detail: dict) -> dict:
    """统一 /skills 输出字段为标准别名键。"""
    if not isinstance(detail, dict):
        return {}

    normalized = dict(detail)
    alias_pairs = (
        ("allowed_tools", "allowed-tools"),
        ("file_patterns", "file-patterns"),
        ("disable_model_invocation", "disable-model-invocation"),
        ("user_invocable", "user-invocable"),
        ("argument_hint", "argument-hint"),
        ("command_dispatch", "command-dispatch"),
        ("command_tool", "command-tool"),
        ("required_mcp_servers", "required-mcp-servers"),
        ("required_mcp_tools", "required-mcp-tools"),
    )
    for snake_key, kebab_key in alias_pairs:
        if kebab_key in detail:
            normalized[kebab_key] = detail[kebab_key]
        elif snake_key in detail:
            normalized[kebab_key] = detail[snake_key]
        normalized.pop(snake_key, None)
    return normalized


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
            console.print("  [dim white]当前没有已加载的 Skillpack。[/dim white]")
            return True
        table = Table(show_header=True, expand=False)
        table.add_column("name", style="#b294bb")
        table.add_column("source", style="#81a2be")
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
            console.print("  [#de935f]用法：/skills get <name>[/#de935f]")
            return True
        name = tokens[2]
        detail = engine.get_skillpack_detail(name)
        detail = _to_standard_skill_detail(detail)
        console.print(
            json.dumps(detail, ensure_ascii=False, indent=2)
        )
        return True

    if sub == "create":
        if len(tokens) < 5:
            console.print(
                "  [#de935f]用法：/skills create <name> --json '<payload>' "
                "或 --json-file <path>[/#de935f]"
            )
            return True
        name = tokens[2]
        payload = _parse_skills_payload_options(tokens, 3)
        detail = engine.create_skillpack(name, payload, actor="cli")
        detail = _to_standard_skill_detail(detail)
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
                "  [#de935f]用法：/skills patch <name> --json '<payload>' "
                "或 --json-file <path>[/#de935f]"
            )
            return True
        name = tokens[2]
        payload = _parse_skills_payload_options(tokens, 3)
        detail = engine.patch_skillpack(name, payload, actor="cli")
        detail = _to_standard_skill_detail(detail)
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
            console.print("  [#de935f]用法：/skills delete <name> [--yes][/#de935f]")
            return True
        name = tokens[2]
        flags = set(tokens[3:])
        if flags - {"--yes"}:
            console.print("  [#de935f]仅支持参数：--yes[/#de935f]")
            return True
        if "--yes" not in flags:
            console.print("  [#de935f]删除需确认，请追加 `--yes`。[/#de935f]")
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
        "  [#de935f]未知 /skills 子命令。可用：list/get/create/patch/delete[/#de935f]"
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


def _sync_model_suggestions(engine: AgentEngine) -> None:
    """将可用模型名称同步到 /model 命令的补全参数。"""
    global _MODEL_ARGUMENTS
    names = engine.model_names()
    _MODEL_ARGUMENTS = tuple(["list"] + names)


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
    command_arguments: dict[str, tuple[str, ...]] = {
        alias: _FULL_ACCESS_ARGUMENTS for alias in _FULL_ACCESS_COMMAND_ALIASES
    }
    command_arguments.update(
        {alias: _SUBAGENT_ARGUMENTS for alias in _SUBAGENT_COMMAND_ALIASES}
    )
    command_arguments.update(
        {alias: _PLAN_ARGUMENTS for alias in _PLAN_COMMAND_ALIASES}
    )
    command_arguments.update(
        {alias: _MODEL_ARGUMENTS for alias in _MODEL_COMMAND_ALIASES}
    )
    command_arguments.update(
        {alias: _CONFIG_ARGUMENTS for alias in _CONFIG_COMMAND_ALIASES}
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


def _render_welcome(
    config: "ExcelManusConfig", skill_count: int, mcp_count: int = 0
) -> None:
    """渲染欢迎信息面板 — 含 Logo、版本、模型、技能包、MCP 信息。"""
    from excelmanus.config import ExcelManusConfig  # noqa: F811 避免循环导入

    # 构建信息区
    info = Text()
    info.append(_LOGO, style="bold green")
    info.append(f"\n  v{__version__}", style="bold white")
    info.append("  ·  基于大语言模型的 Excel 智能代理\n\n", style="dim white")

    # 环境信息
    model_display = config.model
    info.append("  模型  ", style="dim white")
    info.append(f"{model_display}\n", style="bold #f0c674")
    info.append("  技能  ", style="dim white")
    info.append(f"{skill_count} 个 Skillpack 已加载\n", style="bold #b5bd68")
    info.append("  子代理  ", style="dim white")
    info.append(
        ("已启用" if config.subagent_enabled else "已禁用") + "\n",
        style="bold #81a2be" if config.subagent_enabled else "bold #cc6666",
    )
    # MCP 状态
    info.append("  MCP   ", style="dim white")
    if mcp_count > 0:
        info.append(f"{mcp_count} 个 Server 已连接\n", style="bold #b294bb")
    else:
        info.append("未配置\n", style="dim white")
    info.append("  目录  ", style="dim white")
    info.append(f"{os.path.abspath(config.workspace_root)}\n\n", style="white")

    # 快捷命令
    info.append("  命令  ", style="dim white")
    info.append("/help", style="#b5bd68")
    info.append("  /history", style="#b5bd68")
    info.append("  /clear", style="#b5bd68")
    info.append("  /skills", style="#b5bd68")
    info.append("  /subagent", style="#b5bd68")
    info.append("  /mcp", style="#b5bd68")
    info.append("  /config", style="#b5bd68")
    info.append("  /fullAccess", style="#b5bd68")
    info.append("  /accept <id>", style="#b5bd68")
    info.append("  /reject <id>", style="#b5bd68")
    info.append("  /undo <id>", style="#b5bd68")
    info.append("  /plan", style="#b5bd68")
    info.append("  /model", style="#b5bd68")
    info.append("  /<skill_name>", style="#b5bd68")
    info.append("  exit\n", style="#b5bd68")

    console.print(
        Panel(
            info,
            border_style="#5f875f",
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


# ------------------------------------------------------------------
# 交互式问题选择器（箭头键导航）
# ------------------------------------------------------------------

class _InteractiveSelectResult:
    """交互式选择器的返回结果。"""

    def __init__(
        self,
        *,
        selected_indices: list[int] | None = None,
        other_text: str | None = None,
        escaped: bool = False,
    ) -> None:
        self.selected_indices = selected_indices or []
        self.other_text = other_text
        self.escaped = escaped


async def _interactive_question_select(
    question: "PendingQuestion",
) -> _InteractiveSelectResult | None:
    """使用 prompt_toolkit 构建箭头键导航的交互式选择器。

    单选：↑↓ 移动光标，Enter 确认。
    多选：↑↓ 移动光标，Space 切换选中，Enter 提交。
    Other 选项：选中后 Enter 进入文本输入。
    Esc：退出选择器，回到普通输入框。

    返回 None 表示不支持交互式选择（非交互终端或无 prompt_toolkit）。
    返回 _InteractiveSelectResult.escaped=True 表示用户按了 Esc。
    """
    if not _PROMPT_TOOLKIT_ENABLED or not _is_interactive_terminal():
        return None

    options = question.options
    if not options:
        return None

    multi = question.multi_select
    cursor = [0]
    checked: set[int] = set()  # 多选模式下已选中的索引
    result_holder: list[_InteractiveSelectResult] = []

    kb = KeyBindings()

    @kb.add("up")
    def _move_up(event) -> None:  # type: ignore[no-untyped-def]
        cursor[0] = (cursor[0] - 1) % len(options)

    @kb.add("down")
    def _move_down(event) -> None:  # type: ignore[no-untyped-def]
        cursor[0] = (cursor[0] + 1) % len(options)

    @kb.add("space")
    def _toggle(event) -> None:  # type: ignore[no-untyped-def]
        if multi:
            idx = cursor[0]
            # Other 选项不参与 space 切换
            if options[idx].is_other:
                return
            if idx in checked:
                checked.discard(idx)
            else:
                checked.add(idx)

    @kb.add("enter")
    def _confirm(event) -> None:  # type: ignore[no-untyped-def]
        idx = cursor[0]
        opt = options[idx]
        if opt.is_other:
            # Other 选项：标记需要文本输入
            result_holder.append(
                _InteractiveSelectResult(
                    selected_indices=sorted(checked) if multi else [],
                    other_text="__NEED_INPUT__",
                )
            )
            event.app.exit()
            return
        if multi:
            # 多选模式：Enter 提交当前已选（如果光标处未选中则也加入）
            if idx not in checked:
                checked.add(idx)
            result_holder.append(
                _InteractiveSelectResult(selected_indices=sorted(checked))
            )
        else:
            # 单选模式：直接确认光标处选项
            result_holder.append(
                _InteractiveSelectResult(selected_indices=[idx])
            )
        event.app.exit()

    @kb.add("escape")
    def _escape(event) -> None:  # type: ignore[no-untyped-def]
        result_holder.append(_InteractiveSelectResult(escaped=True))
        event.app.exit()

    # 构建动态文本控件
    def _get_formatted_text() -> FormattedText:
        """生成选择器的格式化文本。"""
        fragments: list[tuple[str, str]] = []
        # 标题行
        header = question.header or "待确认"
        fragments.append(("class:header", f"  ❓ {header}\n"))
        if question.text:
            fragments.append(("class:text", f"  {question.text}\n"))
        fragments.append(("", "\n"))

        for i, opt in enumerate(options):
            is_cursor = i == cursor[0]
            is_checked = i in checked

            # 前缀指示器
            if multi:
                if is_checked:
                    marker = "◉" if is_cursor else "●"
                else:
                    marker = "○" if is_cursor else "○"
                prefix = f"  {'❯' if is_cursor else ' '} {marker} "
            else:
                prefix = f"  {'❯' if is_cursor else ' '} "

            # 选项文本
            label = opt.label
            desc = f" — {opt.description}" if opt.description else ""
            line = f"{prefix}{i + 1}. {label}{desc}\n"

            if is_cursor:
                style = "class:selected"
            elif is_checked:
                style = "class:checked"
            else:
                style = "class:option"
            fragments.append((style, line))

        # 底部提示
        fragments.append(("", "\n"))
        if multi:
            fragments.append(
                ("class:hint", "  ↑↓ 移动  Space 选中/取消  Enter 提交  Esc 退出\n")
            )
        else:
            fragments.append(
                ("class:hint", "  ↑↓ 移动  Enter 确认  Esc 退出\n")
            )
        return FormattedText(fragments)

    control = FormattedTextControl(_get_formatted_text)
    window = Window(content=control, always_hide_cursor=True)
    layout = Layout(HSplit([window]))

    style = Style.from_dict(
        {
            "header": "bold #f0c674",
            "text": "",
            "selected": "bold #b5bd68 reverse",
            "checked": "bold #b5bd68",
            "option": "",
            "hint": "italic #888888",
        }
    )

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=False,
    )

    await app.run_async()

    if not result_holder:
        return _InteractiveSelectResult(escaped=True)

    result = result_holder[0]

    # 处理 Other 选项：需要文本输入
    if result.other_text == "__NEED_INPUT__":
        console.print("  [dim white]请输入自定义内容：[/dim white]")
        try:
            other_input = (await _read_user_input()).strip()
        except (KeyboardInterrupt, EOFError):
            return _InteractiveSelectResult(escaped=True)
        if not other_input:
            return _InteractiveSelectResult(escaped=True)
        return _InteractiveSelectResult(
            selected_indices=result.selected_indices,
            other_text=other_input,
        )

    return result


def _build_answer_from_select(
    question: "PendingQuestion",
    result: _InteractiveSelectResult,
) -> str:
    """将交互式选择结果转换为引擎可识别的回答文本。"""
    if result.other_text is not None:
        if question.multi_select:
            parts = [str(idx + 1) for idx in result.selected_indices]
            other_text = result.other_text.strip()
            if other_text:
                parts.append(other_text)
            return "\n".join(parts)
        return result.other_text

    if not result.selected_indices:
        return ""

    # 用编号回答，引擎的 parse_answer 支持编号匹配
    parts = [str(idx + 1) for idx in result.selected_indices]
    if question.multi_select:
        return "\n".join(parts)
    return parts[0]


# ------------------------------------------------------------------
# 审批交互式选择器
# ------------------------------------------------------------------

# 审批选项常量
_APPROVAL_OPTION_ACCEPT = "执行"
_APPROVAL_OPTION_REJECT = "拒绝"
_APPROVAL_OPTION_FULLACCESS = "全部授权"

_APPROVAL_OPTIONS: list[tuple[str, str, str]] = [
    ("✅ 执行", "确认并执行此操作", _APPROVAL_OPTION_ACCEPT),
    ("❌ 拒绝", "取消此操作", _APPROVAL_OPTION_REJECT),
    ("🔓 全部授权", "开启 fullAccess 后自动执行", _APPROVAL_OPTION_FULLACCESS),
]


async def _interactive_approval_select(
    pending: "PendingApproval",
) -> str | None:
    """使用 prompt_toolkit 构建审批交互式选择器（与 ask_user 风格一致）。

    ↑↓ 移动光标，Enter 确认。
    Esc：退出选择器，回到普通输入框。

    返回 None 表示不支持交互式选择或用户按了 Esc。
    返回 _APPROVAL_OPTION_ACCEPT / _APPROVAL_OPTION_REJECT / _APPROVAL_OPTION_FULLACCESS。
    """
    if not _PROMPT_TOOLKIT_ENABLED or not _is_interactive_terminal():
        return None

    cursor = [0]
    result_holder: list[str | None] = []

    kb = KeyBindings()

    @kb.add("up")
    def _move_up(event) -> None:  # type: ignore[no-untyped-def]
        cursor[0] = (cursor[0] - 1) % len(_APPROVAL_OPTIONS)

    @kb.add("down")
    def _move_down(event) -> None:  # type: ignore[no-untyped-def]
        cursor[0] = (cursor[0] + 1) % len(_APPROVAL_OPTIONS)

    @kb.add("enter")
    def _confirm(event) -> None:  # type: ignore[no-untyped-def]
        result_holder.append(_APPROVAL_OPTIONS[cursor[0]][2])
        event.app.exit()

    @kb.add("escape")
    def _escape(event) -> None:  # type: ignore[no-untyped-def]
        result_holder.append(None)
        event.app.exit()

    # 构建参数摘要
    args = pending.arguments or {}
    args_parts: list[str] = []
    for key in ("file_path", "sheet_name", "script", "command"):
        val = args.get(key)
        if val is not None:
            display = str(val)
            if len(display) > 60:
                display = display[:57] + "..."
            args_parts.append(f"{key}={display}")
    args_summary = ", ".join(args_parts) if args_parts else ""

    def _get_formatted_text() -> FormattedText:
        """生成审批选择器的格式化文本。"""
        fragments: list[tuple[str, str]] = []
        fragments.append(("class:header", "  ⚠️ 检测到高风险操作\n"))
        fragments.append(("class:text", f"  工具: {pending.tool_name}\n"))
        fragments.append(("class:text", f"  ID: {pending.approval_id}\n"))
        if args_summary:
            fragments.append(("class:text", f"  参数: {args_summary}\n"))
        fragments.append(("", "\n"))

        for i, (label, desc, _value) in enumerate(_APPROVAL_OPTIONS):
            is_cursor = i == cursor[0]
            prefix = f"  {'❯' if is_cursor else ' '} "
            line = f"{prefix}{i + 1}. {label} — {desc}\n"
            style = "class:selected" if is_cursor else "class:option"
            fragments.append((style, line))

        fragments.append(("", "\n"))
        fragments.append(("class:hint", "  ↑↓ 移动  Enter 确认  Esc 退出\n"))
        return FormattedText(fragments)

    control = FormattedTextControl(_get_formatted_text)
    window = Window(content=control, always_hide_cursor=True)
    layout = Layout(HSplit([window]))

    style = Style.from_dict(
        {
            "header": "bold #f0c674",
            "text": "",
            "selected": "bold #b5bd68 reverse",
            "option": "",
            "hint": "italic #888888",
        }
    )

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=False,
    )

    await app.run_async()

    if not result_holder:
        return None
    return result_holder[0]


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
    table.add_column("命令", style="#b5bd68", min_width=14)
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
    table.add_row("/mcp", "查看 MCP Server 连接状态与工具列表")
    table.add_row("/config", "列出 MCP 引用的环境变量配置（脱敏）")
    table.add_row("/config set <KEY> <VALUE>", "设置环境变量到 .env 文件")
    table.add_row("/config get <KEY>", "查看某个环境变量的值（脱敏）")
    table.add_row("/config delete <KEY>", "从 .env 文件删除某个环境变量")
    table.add_row("/fullAccess [on|off|status]", "会话级代码技能权限控制")
    table.add_row("/accept <id>", "执行待确认高风险操作")
    table.add_row("/reject <id>", "拒绝待确认高风险操作")
    table.add_row("/undo <id>", "回滚已确认且可回滚的操作")
    table.add_row("/plan [on|off|status]", "会话级 plan mode 开关与状态")
    table.add_row("/plan approve [plan_id]", "批准待审批计划并自动继续执行")
    table.add_row("/plan reject [plan_id]", "拒绝待审批计划")
    table.add_row("/model", "查看当前模型")
    table.add_row("/model list", "列出所有可用模型")
    table.add_row("/model <name>", "切换模型（支持智能补全）")
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
            border_style="#5f87af",
            expand=False,
            padding=(1, 2),
            subtitle="[dim white]直接输入自然语言即可与代理对话[/dim white]",
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
            history_entries.append(f"  [bold #81a2be]◂[/bold #81a2be] {display}")

    if not history_entries:
        console.print("  [dim white]暂无对话历史。[/dim white]")
        return

    console.print()
    console.print(
        Panel(
            "\n".join(history_entries),
            title=f"[bold]对话历史[/bold] [dim white]({len(history_entries)} 条)[/dim white]",
            title_align="left",
            border_style="#de935f",
            expand=False,
            padding=(1, 1),
        )
    )
    console.print()


def _render_farewell() -> None:
    """渲染告别信息。"""
    console.print("\n  [#81a2be]感谢使用 ExcelManus，再见！[/#81a2be] 👋\n")


def _render_skills(engine: AgentEngine) -> None:
    """渲染已加载 Skillpack 与最近一次路由结果。"""
    loaded = engine.list_loaded_skillpacks()
    route = engine.last_route_result

    table = Table(show_header=False, show_edge=False, pad_edge=False, expand=False)
    table.add_column(style="dim white", min_width=12)
    table.add_column()

    table.add_row(
        "已加载",
        ", ".join(f"[#b294bb]{s}[/#b294bb]" for s in loaded) if loaded else "[dim white]无[/dim white]",
    )
    table.add_row("路由模式", f"[#f0c674]{route.route_mode}[/#f0c674]")
    table.add_row(
        "命中技能",
        ", ".join(f"[bold]{s}[/bold]" for s in route.skills_used)
        if route.skills_used
        else "[dim white]无[/dim white]",
    )
    tool_count = len(route.tool_scope) if route.tool_scope else 0
    table.add_row("工具范围", f"{tool_count} 个工具")
    permission = "full_access" if engine.full_access_enabled else "restricted"
    table.add_row("代码技能权限", permission)
    table.add_row(
        "子代理状态",
        "enabled" if engine.subagent_enabled else "disabled",
    )
    table.add_row(
        "计划模式",
        "enabled" if engine.plan_mode_enabled else "disabled",
    )

    console.print()
    console.print(
        Panel(
            table,
            title="[bold]🧩 Skillpacks[/bold]",
            title_align="left",
            border_style="#b294bb",
            expand=False,
            padding=(0, 2),
        )
    )
    console.print()
def _render_mcp(engine: AgentEngine) -> None:
    """渲染 MCP Server 连接状态与工具列表。"""
    servers = engine.mcp_server_info()

    if not servers:
        console.print()
        console.print("  [dim white]未配置或未连接任何 MCP Server。[/dim white]")
        console.print()
        return

    table = Table(
        show_header=True, show_edge=False, pad_edge=False, expand=False
    )
    table.add_column("Server", style="#b294bb", min_width=16)
    table.add_column("状态", style="#81a2be", min_width=10)
    table.add_column("传输", style="#f0c674", min_width=8)
    table.add_column("工具数", style="#b5bd68", min_width=6, justify="right")
    table.add_column("错误", style="#cc6666", min_width=12)
    table.add_column("工具列表", style="white")

    for srv in servers:
        tool_names = srv.get("tools", [])
        status = str(srv.get("status", "unknown"))
        last_error = str(srv.get("last_error", "") or "-")
        # 工具名过多时截断显示
        if len(tool_names) <= 6:
            tools_display = ", ".join(tool_names) if tool_names else "-"
        else:
            shown = ", ".join(tool_names[:6])
            tools_display = f"{shown} … (+{len(tool_names) - 6})"
        table.add_row(
            srv["name"],
            status,
            srv.get("transport", "?"),
            str(srv.get("tool_count", 0)),
            last_error,
            tools_display,
        )

    console.print()
    console.print(
        Panel(
            table,
            title="[bold]🔌 MCP Servers[/bold]",
            title_align="left",
            border_style="#b294bb",
            expand=False,
            padding=(0, 2),
        )
    )
    console.print()


# ------------------------------------------------------------------
# /config 命令：MCP 工具环境变量配置管理
# ------------------------------------------------------------------

# 匹配 $VAR 或 ${VAR} 引用
_CONFIG_ENV_REF_PATTERN = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def _scan_mcp_env_vars(workspace_root: str = ".") -> list[str]:
    """扫描 mcp.json 中引用的所有 $VAR 环境变量名（去重保序）。"""
    from excelmanus.mcp.config import MCPConfigLoader  # 避免循环导入

    # 按 MCPConfigLoader 的搜索优先级查找配置文件
    candidates: list[Path] = []
    env_path = os.environ.get("EXCELMANUS_MCP_CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path(workspace_root) / "mcp.json")
    candidates.append(Path("~/.excelmanus/mcp.json").expanduser())

    data: dict | None = None
    for path in candidates:
        resolved = path.expanduser().resolve()
        if resolved.is_file():
            try:
                with open(resolved, "r", encoding="utf-8") as f:
                    data = json.load(f)
                break
            except (json.JSONDecodeError, OSError):
                continue

    if not data or not isinstance(data.get("mcpServers"), dict):
        return []

    # 递归扫描所有字符串值中的环境变量引用
    seen: set[str] = set()
    ordered: list[str] = []

    def _scan(value: object) -> None:
        if isinstance(value, str):
            for match in _CONFIG_ENV_REF_PATTERN.finditer(value):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    ordered.append(name)
        elif isinstance(value, dict):
            for v in value.values():
                _scan(v)
        elif isinstance(value, list):
            for item in value:
                _scan(item)

    _scan(data["mcpServers"])
    return ordered


def _mask_secret(value: str) -> str:
    """对敏感值脱敏：保留前4位和后4位，中间用 **** 替代。"""
    if len(value) <= 12:
        return value[:3] + "****" + value[-2:] if len(value) > 5 else "****"
    return value[:4] + "****" + value[-4:]


def _dotenv_path(workspace_root: str = ".") -> Path:
    """返回工作区 .env 文件路径。"""
    return Path(workspace_root).resolve() / ".env"


def _read_dotenv_lines(dotenv_file: Path) -> list[str]:
    """读取 .env 文件的所有行（文件不存在返回空列表）。"""
    if not dotenv_file.is_file():
        return []
    return dotenv_file.read_text(encoding="utf-8").splitlines()


def _write_dotenv_lines(dotenv_file: Path, lines: list[str]) -> None:
    """将行列表写回 .env 文件。"""
    dotenv_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _dotenv_set(dotenv_file: Path, key: str, value: str) -> None:
    """在 .env 文件中设置或更新一个键值对。"""
    lines = _read_dotenv_lines(dotenv_file)
    pattern = re.compile(rf"^{re.escape(key)}\s*=")
    new_line = f"{key}={value}"
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        # 在文件末尾追加（如果最后一行非空则加空行）
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(new_line)
    _write_dotenv_lines(dotenv_file, lines)
    # 同步到当前进程环境变量
    os.environ[key] = value


def _dotenv_delete(dotenv_file: Path, key: str) -> bool:
    """从 .env 文件中删除一个键。返回是否找到并删除。"""
    lines = _read_dotenv_lines(dotenv_file)
    pattern = re.compile(rf"^{re.escape(key)}\s*=")
    new_lines = [line for line in lines if not pattern.match(line)]
    if len(new_lines) == len(lines):
        return False
    _write_dotenv_lines(dotenv_file, new_lines)
    os.environ.pop(key, None)
    return True


def _handle_config_command(user_input: str, workspace_root: str = ".") -> bool:
    """处理 /config 命令。返回 True 表示已处理。"""
    stripped = user_input.strip()
    lowered = stripped.lower()

    # /config 或 /config list — 列出 MCP 引用的环境变量及其状态
    if lowered in ("/config", "/config list"):
        env_vars = _scan_mcp_env_vars(workspace_root)
        if not env_vars:
            console.print()
            console.print("  [dim white]mcp.json 中未发现环境变量引用。[/dim white]")
            console.print()
            return True

        table = Table(
            show_header=True, show_edge=False, pad_edge=False, expand=False
        )
        table.add_column("变量名", style="#b294bb", min_width=20)
        table.add_column("状态", style="#81a2be", min_width=8)
        table.add_column("值（脱敏）", style="white")

        for var_name in env_vars:
            value = os.environ.get(var_name)
            if value:
                table.add_row(var_name, "[green]已设置[/green]", _mask_secret(value))
            else:
                table.add_row(var_name, "[#cc6666]未设置[/#cc6666]", "-")

        console.print()
        console.print(
            Panel(
                table,
                title="[bold]🔑 MCP 环境变量配置[/bold]",
                title_align="left",
                border_style="#f0c674",
                expand=False,
                padding=(0, 2),
            )
        )
        console.print(
            "  [dim white]使用 /config set <KEY> <VALUE> 设置，"
            "/config delete <KEY> 删除[/dim white]"
        )
        console.print()
        return True

    # /config set <KEY> <VALUE>
    if lowered.startswith("/config set "):
        parts = stripped.split(None, 3)  # ["/config", "set", KEY, VALUE]
        if len(parts) < 4:
            console.print(
                "  [#de935f]用法：/config set <KEY> <VALUE>[/#de935f]"
            )
            return True
        key = parts[2]
        value = parts[3]
        dotenv_file = _dotenv_path(workspace_root)
        try:
            _dotenv_set(dotenv_file, key, value)
            console.print(
                f"  [green]✓[/green] 已设置 [#b294bb]{key}[/#b294bb] = "
                f"{_mask_secret(value)}"
            )
            console.print(
                "  [dim white]已写入 .env 并同步到当前进程。"
                "MCP Server 需重启后生效。[/dim white]"
            )
        except OSError as exc:
            console.print(f"  [red]✗ 写入 .env 失败：{exc}[/red]")
        return True

    # /config get <KEY>
    if lowered.startswith("/config get "):
        parts = stripped.split(None, 2)
        if len(parts) < 3:
            console.print("  [#de935f]用法：/config get <KEY>[/#de935f]")
            return True
        key = parts[2]
        value = os.environ.get(key)
        if value:
            console.print(
                f"  [#b294bb]{key}[/#b294bb] = {_mask_secret(value)}"
            )
        else:
            console.print(
                f"  [#b294bb]{key}[/#b294bb] [#cc6666]未设置[/#cc6666]"
            )
        return True

    # /config delete <KEY>
    if lowered.startswith("/config delete "):
        parts = stripped.split(None, 2)
        if len(parts) < 3:
            console.print("  [#de935f]用法：/config delete <KEY>[/#de935f]")
            return True
        key = parts[2]
        dotenv_file = _dotenv_path(workspace_root)
        try:
            deleted = _dotenv_delete(dotenv_file, key)
            if deleted:
                console.print(
                    f"  [green]✓[/green] 已从 .env 删除 [#b294bb]{key}[/#b294bb]"
                )
            else:
                console.print(
                    f"  [dim white]{key} 在 .env 中不存在。[/dim white]"
                )
        except OSError as exc:
            console.print(f"  [red]✗ 写入 .env 失败：{exc}[/red]")
        return True

    # 未知子命令
    console.print(
        "  [#de935f]未知 /config 子命令。可用：list / set / get / delete[/#de935f]"
    )
    return True


def _is_interactive_terminal() -> bool:
    """判断当前是否交互式终端。"""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


# ------------------------------------------------------------------
# 交互式模型选择器（箭头键导航 + Enter 确认切换）
# ------------------------------------------------------------------

async def _interactive_model_select(engine: AgentEngine) -> str | None:
    """使用 prompt_toolkit 构建交互式模型选择器。

    ↑↓ 移动光标，Enter 确认切换，Esc 退出。
    返回选中模型的 name（如 "default"、"libao-kimi"），
    返回 None 表示用户按了 Esc 或不支持交互式选择。
    """
    if not _PROMPT_TOOLKIT_ENABLED or not _is_interactive_terminal():
        return None

    rows = engine.list_models()
    if not rows:
        return None

    # 找到当前激活模型的索引作为初始光标位置
    initial_cursor = 0
    for i, row in enumerate(rows):
        if row.get("active"):
            initial_cursor = i
            break

    cursor = [initial_cursor]
    result_holder: list[str | None] = []

    kb = KeyBindings()

    @kb.add("up")
    def _move_up(event) -> None:  # type: ignore[no-untyped-def]
        cursor[0] = (cursor[0] - 1) % len(rows)

    @kb.add("down")
    def _move_down(event) -> None:  # type: ignore[no-untyped-def]
        cursor[0] = (cursor[0] + 1) % len(rows)

    @kb.add("enter")
    def _confirm(event) -> None:  # type: ignore[no-untyped-def]
        result_holder.append(rows[cursor[0]]["name"])
        event.app.exit()

    @kb.add("escape")
    def _escape(event) -> None:  # type: ignore[no-untyped-def]
        result_holder.append(None)
        event.app.exit()

    def _get_formatted_text() -> FormattedText:
        fragments: list[tuple[str, str]] = []
        fragments.append(("class:header", "  🤖 选择模型\n\n"))

        for i, row in enumerate(rows):
            is_cursor = i == cursor[0]
            is_active = bool(row.get("active"))

            prefix = "  ❯ " if is_cursor else "    "
            name = row["name"]
            model = row["model"]
            desc = f"  {row['description']}" if row.get("description") else ""
            marker = " ✦" if is_active else ""
            line = f"{prefix}{name} → {model}{desc}{marker}\n"

            if is_cursor:
                style = "class:selected"
            elif is_active:
                style = "class:active"
            else:
                style = "class:option"
            fragments.append((style, line))

        fragments.append(("", "\n"))
        fragments.append(("class:hint", "  ↑↓ 移动  Enter 确认  Esc 退出\n"))
        return FormattedText(fragments)

    control = FormattedTextControl(_get_formatted_text)
    window = Window(content=control, always_hide_cursor=True)
    layout = Layout(HSplit([window]))

    style = Style.from_dict(
        {
            "header": "bold #f0c674",
            "selected": "bold #b5bd68 reverse",
            "active": "bold #f0c674",
            "option": "",
            "hint": "italic #888888",
        }
    )

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=False,
    )

    await app.run_async()

    if not result_holder:
        return None
    return result_holder[0]


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
            self._console.print(Text(f"{line}{padding}", style="dim white"), end="\r")
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
    _sync_model_suggestions(engine)
    while True:
        has_pending_question = bool(
            getattr(engine, "has_pending_question", lambda: False)()
        )
        waiting_multiselect = bool(
            getattr(engine, "is_waiting_multiselect_answer", lambda: False)()
        )

        # ----------------------------------------------------------
        # 有待回答问题且有选项时，优先启动交互式选择器
        # ----------------------------------------------------------
        if has_pending_question:
            current_q_getter = getattr(engine, "current_pending_question", None)
            current_q: PendingQuestion | None = (
                current_q_getter() if callable(current_q_getter) else None
            )
            if current_q and current_q.options:
                try:
                    select_result = await _interactive_question_select(current_q)
                except (KeyboardInterrupt, EOFError):
                    _render_farewell()
                    return
                except Exception as exc:
                    logger.warning("交互式选择器异常，回退到普通输入：%s", exc)
                    select_result = None

                if select_result is not None and not select_result.escaped:
                    # 用户通过选择器完成了选择
                    user_input = _build_answer_from_select(current_q, select_result)
                    if user_input:
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
                                    border_style="#5f875f",
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
                    # user_input 为空（不应发生），回退到普通输入
                # select_result 为 None（不支持）或 escaped（用户按 Esc）
                # 回退到下方普通输入流程

        # ----------------------------------------------------------
        # 有待确认审批时，启动审批交互式选择器
        # ----------------------------------------------------------
        has_pending_approval = bool(
            getattr(engine, "has_pending_approval", lambda: False)()
        )
        if has_pending_approval and not has_pending_question:
            pending_approval_getter = getattr(engine, "current_pending_approval", None)
            pending_apv: PendingApproval | None = (
                pending_approval_getter() if callable(pending_approval_getter) else None
            )
            if pending_apv is not None:
                try:
                    approval_choice = await _interactive_approval_select(pending_apv)
                except (KeyboardInterrupt, EOFError):
                    _render_farewell()
                    return
                except Exception as exc:
                    logger.warning("审批交互式选择器异常，回退到普通输入：%s", exc)
                    approval_choice = None

                if approval_choice is not None:
                    # 将选择结果转换为对应的引擎命令
                    if approval_choice == _APPROVAL_OPTION_ACCEPT:
                        user_input = f"/accept {pending_apv.approval_id}"
                    elif approval_choice == _APPROVAL_OPTION_REJECT:
                        user_input = f"/reject {pending_apv.approval_id}"
                    elif approval_choice == _APPROVAL_OPTION_FULLACCESS:
                        # 先开启 fullAccess，再 accept
                        user_input = f"/fullAccess on"
                    else:
                        user_input = f"/reject {pending_apv.approval_id}"

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
                                border_style="#5f875f",
                                padding=(1, 2),
                                expand=False,
                            )
                        )
                        # 全部授权模式：开启 fullAccess 后自动 accept
                        if approval_choice == _APPROVAL_OPTION_FULLACCESS:
                            accept_input = f"/accept {pending_apv.approval_id}"
                            renderer2 = StreamRenderer(console)
                            console.print()
                            reply2 = await _chat_with_feedback(
                                engine,
                                user_input=accept_input,
                                renderer=renderer2,
                            )
                            console.print()
                            console.print(
                                Panel(
                                    Markdown(reply2),
                                    border_style="#5f875f",
                                    padding=(1, 2),
                                    expand=False,
                                )
                            )
                    except KeyboardInterrupt:
                        _render_farewell()
                        return
                    except Exception as exc:
                        logger.error("处理审批操作时发生错误: %s", exc, exc_info=True)
                        console.print(f"  [red]✗ 处理审批操作时发生错误：{exc}[/red]")
                    continue
                # approval_choice 为 None（不支持或 Esc），回退到普通输入

        try:
            if waiting_multiselect:
                console.print(
                    "  [dim white]多选回答模式：每行输入一个选项，空行提交。[/dim white]"
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
                        border_style="#5f875f",
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

        if user_input.lower() == "/mcp":
            _render_mcp(engine)
            continue

        if user_input.lower().startswith("/config"):
            _handle_config_command(user_input, engine.config.workspace_root)
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

        # /model 和 /model list 在 CLI 层拦截，使用交互式选择器
        lowered_parts = user_input.lower().split()
        lowered_cmd = lowered_parts[0] if lowered_parts else ""
        if lowered_cmd == "/model" and (
            len(lowered_parts) == 1 or (len(lowered_parts) == 2 and lowered_parts[1] == "list")
        ):
            try:
                selected_name = await _interactive_model_select(engine)
            except (KeyboardInterrupt, EOFError):
                _render_farewell()
                return
            except Exception as exc:
                logger.warning("交互式模型选择器异常，回退到文本列表：%s", exc)
                selected_name = None

            if selected_name is not None:
                result_msg = engine.switch_model(selected_name)
                console.print(f"  [#81a2be]{result_msg}[/#81a2be]")
                _sync_model_suggestions(engine)
            else:
                console.print("  [dim white]已取消选择。[/dim white]")
            continue

        # 会话控制命令统一走 engine.chat（与 API 行为一致）
        if lowered_cmd in _SESSION_CONTROL_COMMAND_ALIASES:
            reply = _reply_text(await engine.chat(user_input))
            console.print(f"  [#81a2be]{reply}[/#81a2be]")
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
                console.print(f"  [#de935f]参数提示：{argument_hint.strip()}[/#de935f]")
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
                        border_style="#5f875f",
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
                f"  [#de935f]未知命令：{user_input}。可用命令示例：{suggestion}[/#de935f]"
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
                    border_style="#5f875f",
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

    # 创建 AgentEngine
    engine = AgentEngine(
        config,
        registry,
        skill_router=router,
        persistent_memory=persistent_memory,
        memory_extractor=memory_extractor,
    )
    _sync_skill_command_suggestions(engine)
    _sync_model_suggestions(engine)

    # 初始化 MCP 连接
    try:
        await engine.initialize_mcp()
    except Exception:
        logger.warning("MCP 初始化失败，已跳过", exc_info=True)

    # 渲染欢迎信息
    skill_count = len(engine.list_loaded_skillpacks())
    mcp_count = engine.mcp_connected_count
    _render_welcome(config, skill_count, mcp_count)

    # 启动 REPL 循环
    try:
        await _repl_loop(engine)
    finally:
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
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        # 顶层捕获 Ctrl+C，确保优雅退出
        _render_farewell()
