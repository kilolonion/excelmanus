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
    "/save",
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
    "/backup",
    "/ui",
}

_FULL_ACCESS_COMMAND_ALIASES = {"/fullaccess", "/full_access"}
_BACKUP_COMMAND_ALIASES = {"/backup"}
_UI_COMMAND_ALIASES = {"/ui"}
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
    | _BACKUP_COMMAND_ALIASES
)
_UI_ARGUMENTS = ("status", "dashboard", "classic")

_SLASH_COMMAND_SUGGESTIONS = (
    "/help",
    "/history",
    "/clear",
    "/save",
    "/skills",
    "/subagent",
    "/sub_agent",
    "/mcp",
    "/config",
    "/fullaccess",
    "/full_access",
    "/accept",
    "/reject",
    "/undo",
    "/plan",
    "/model",
    "/backup",
    "/ui",
)
_CONFIG_ARGUMENTS = ("list", "set", "get", "delete")
_FULL_ACCESS_ARGUMENTS = ("status", "on", "off")
_BACKUP_ARGUMENTS = ("status", "on", "off", "apply", "list")
_UI_ARGUMENTS_TUPLE = ("status", "dashboard", "classic")
_SUBAGENT_ARGUMENTS = ("status", "on", "off", "list", "run")
_PLAN_ARGUMENTS = ("status", "on", "off", "approve", "reject")
_MODEL_ARGUMENTS: tuple[str, ...] = ("list",)  # 动态模型名称在运行时追加
_DYNAMIC_SKILL_SLASH_COMMANDS: tuple[str, ...] = ()

# --save 启动参数：退出时自动保存对话记录的路径（None 表示未启用）
_AUTO_SAVE_PATH: str | None = None

# 会话级布局模式（初始值从配置加载，可通过 /ui 切换）
_current_layout_mode: str = "dashboard"


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


def _build_prompt_badges(
    *,
    model_hint: str = "",
    turn_number: int = 0,
    layout_mode: str = "dashboard",
    subagent_active: bool = False,
    plan_mode: bool = False,
) -> str:
    """构建 prompt 密集徽章字符串（纯文本，用于 ANSI prompt）。"""
    if layout_mode == "dashboard":
        # Dashboard: 紧凑状态栏风格，用 │ 分隔
        segments: list[str] = []
        if model_hint:
            segments.append(model_hint)
        segments.append(f"T{turn_number}" if turn_number > 0 else "T0")
        flags: list[str] = []
        if subagent_active:
            flags.append("🧵sub")
        if plan_mode:
            flags.append("📋plan")
        if flags:
            segments.append(" ".join(flags))
        return " │ ".join(segments)
    else:
        # Classic: 简洁风格
        parts: list[str] = []
        if model_hint:
            parts.append(model_hint)
        if turn_number > 0:
            parts.append(f"#{turn_number}")
        return " ".join(parts)


def _suggest_similar_commands(user_input: str, *, max_results: int = 3) -> list[str]:
    """基于编辑距离返回最相似的已知命令（最多 max_results 个）。

    使用简单的前缀+子序列匹配，无需外部依赖。
    """
    cmd = user_input.lower().split()[0] if user_input.strip() else ""
    if not cmd:
        return []
    known = _list_known_slash_commands()
    scored: list[tuple[float, str]] = []
    for candidate in known:
        score = _command_similarity(cmd, candidate.lower())
        if score > 0:
            scored.append((score, candidate))
    scored.sort(key=lambda x: -x[0])
    return [s[1] for s in scored[:max_results]]


def _command_similarity(a: str, b: str) -> float:
    """计算两个命令字符串的相似度分数（0~1）。

    结合前缀匹配和编辑距离。
    """
    if a == b:
        return 1.0
    # 前缀匹配加分
    prefix_len = 0
    for ca, cb in zip(a, b):
        if ca == cb:
            prefix_len += 1
        else:
            break
    prefix_score = prefix_len / max(len(a), len(b)) if max(len(a), len(b)) > 0 else 0

    # 编辑距离
    dist = _edit_distance(a, b)
    max_len = max(len(a), len(b))
    edit_score = 1.0 - (dist / max_len) if max_len > 0 else 0

    # 阈值过滤：编辑距离太大的不推荐
    if edit_score < 0.3:
        return 0.0

    return 0.4 * prefix_score + 0.6 * edit_score


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein 编辑距离。"""
    if len(a) > len(b):
        a, b = b, a
    prev = list(range(len(a) + 1))
    for j in range(1, len(b) + 1):
        curr = [j] + [0] * len(a)
        for i in range(1, len(a) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[i] = min(curr[i - 1] + 1, prev[i] + 1, prev[i - 1] + cost)
        prev = curr
    return prev[len(a)]


# ASCII Logo — 逐行渐变色渲染
_LOGO_LINES = [
    r"  ______               _ __  __",
    r" |  ____|             | |  \/  |",
    r" | |__  __  _____ ___ | | \  / | __ _ _ __  _   _ ___",
    " |  __| \\ \\/ / __/ _ \\| | |\\/| |/ _` | '_ \\| | | / __|",
    " | |____ >  < (_|  __/| | |  | | (_| | | | | |_| \\__ \\",
    " |______/_/\\_\\___\\___||_|_|  |_|\\__,_|_| |_|\\__,_|___/",
]

# 绿色→青色渐变色带
_LOGO_GRADIENT = [
    "#5fff87",  # 亮绿
    "#5fd7af",  # 绿青
    "#5fd7d7",  # 青
    "#5fafd7",  # 青蓝
    "#5f87d7",  # 蓝
    "#8787d7",  # 紫蓝
]


def _render_gradient_logo() -> None:
    """渲染渐变色 ASCII Logo。"""
    for i, line in enumerate(_LOGO_LINES):
        color = _LOGO_GRADIENT[i % len(_LOGO_GRADIENT)]
        console.print(Text(line, style=f"bold {color}"), highlight=False)
    console.print()


def _compute_inline_suggestion(user_input: str) -> str | None:
    """根据当前输入计算可追加的补全文本（返回后缀）。"""
    if not user_input.startswith("/"):
        return None

    command, separator, remainder = user_input.partition(" ")
    lowered_command = command.lower()

    # 先补全命令本体：如 /ful -> /fullaccess
    if not separator:
        for suggestion in _list_known_slash_commands():
            if suggestion.lower() == lowered_command:
                return None
            if suggestion.lower().startswith(lowered_command):
                return suggestion[len(user_input) :]
        return None

    # 再补全控制命令参数：如 /fullaccess s -> /fullaccess status
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
        {alias: _BACKUP_ARGUMENTS for alias in _BACKUP_COMMAND_ALIASES}
    )
    command_arguments.update(
        {alias: _MODEL_ARGUMENTS for alias in _MODEL_COMMAND_ALIASES}
    )
    command_arguments.update(
        {alias: _CONFIG_ARGUMENTS for alias in _CONFIG_COMMAND_ALIASES}
    )
    command_arguments.update(
        {alias: _UI_ARGUMENTS_TUPLE for alias in _UI_COMMAND_ALIASES}
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
    """渲染欢迎信息面板 — 环境摘要与快捷命令。"""
    from excelmanus.config import ExcelManusConfig  # noqa: F811 避免循环导入

    info = Text()

    # ── 环境信息区 ──
    label_style = "dim white"
    info.append("  模型      ", style=label_style)
    info.append(f"{config.model}\n", style="bold #f0c674")
    info.append("  子代理    ", style=label_style)
    info.append(
        ("已启用" if config.subagent_enabled else "已禁用") + "\n",
        style="bold #81a2be" if config.subagent_enabled else "dim #cc6666",
    )
    info.append("  工作目录  ", style=label_style)
    info.append(f"{os.path.abspath(config.workspace_root)}\n", style="white")
    # 布局模式
    _mode = _current_layout_mode
    info.append("  布局      ", style=label_style)
    if _mode == "dashboard":
        info.append("dashboard", style="bold #5fd7af")
        info.append("  密集信息模式\n", style="dim white")
    else:
        info.append("classic", style="bold #f0c674")
        info.append("  传统流式模式\n", style="dim white")

    # ── 分隔线 ──
    info.append("  " + "─" * 52 + "\n", style="dim #5f5f5f")

    # ── 快捷命令区（按类别分组）──
    cmd_groups: list[tuple[str, list[str]]] = [
        ("对话", ["/help", "/history", "/clear", "exit"]),
        ("技能", ["/skills", "/model", "/mcp", "/config"]),
        ("控制", ["/subagent", "/fullaccess", "/backup", "/plan"]),
        ("审批", ["/accept", "/reject", "/undo"]),
        ("显示", ["/ui dashboard", "/ui classic"]),
    ]
    for group_name, cmds in cmd_groups:
        info.append(f"  {group_name}  ", style="dim #888888")
        info.append("  ".join(cmds), style="#b5bd68")
        info.append("\n")

    console.print(
        Panel(
            info,
            border_style="#5f875f",
            padding=(0, 1),
            title="[bold #5fd7af]ExcelManus[/bold #5fd7af]",
            title_align="left",
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
    ("🔓 全部授权", "开启 fullaccess 后自动执行", _APPROVAL_OPTION_FULLACCESS),
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


async def _read_user_input(
    *,
    model_hint: str = "",
    turn_number: int = 0,
    subagent_active: bool = False,
    plan_mode: bool = False,
) -> str:
    """读取用户输入：优先使用 prompt_toolkit 的异步输入能力。"""
    # 构建密集徽章提示符
    badges = _build_prompt_badges(
        model_hint=model_hint,
        turn_number=turn_number,
        layout_mode=_current_layout_mode,
        subagent_active=subagent_active,
        plan_mode=plan_mode,
    )
    if _current_layout_mode == "dashboard" and badges:
        # Dashboard: 青色箭头 + dim 分隔符状态栏
        ansi_prompt = f"\n \x1b[2;36m{badges}\x1b[0m \x1b[1;36m▶\x1b[0m "
        rich_prompt = f"\n [dim cyan]{badges}[/dim cyan] [bold cyan]▶[/bold cyan] "
    elif badges:
        # Classic: 绿色箭头
        ansi_prompt = f"\n \x1b[2;37m{badges}\x1b[0m \x1b[1;32m❯\x1b[0m "
        rich_prompt = f"\n [dim white]{badges}[/dim white] [bold green]❯[/bold green] "
    else:
        ansi_prompt = "\n \x1b[1;32m❯\x1b[0m "
        rich_prompt = "\n [bold green]❯[/bold green] "

    if (
        _PROMPT_TOOLKIT_ENABLED
        and _PROMPT_SESSION is not None
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    ):
        try:
            return await _PROMPT_SESSION.prompt_async(ANSI(ansi_prompt))
        except (KeyboardInterrupt, EOFError):
            raise
        except Exception as exc:  # pragma: no cover - 仅保护交互式边界
            logger.warning("prompt_toolkit 输入失败，回退到基础输入：%s", exc)

    return console.input(rich_prompt)


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
    """渲染帮助信息（按分类分区展示）。"""

    def _section_table() -> Table:
        t = Table(show_header=False, show_edge=False, pad_edge=False, expand=False)
        t.add_column("命令", style="#b5bd68", min_width=20)
        t.add_column("说明", style="white")
        return t

    # ── 对话与导航 ──
    t1 = _section_table()
    t1.add_row("/help", "显示此帮助信息")
    t1.add_row("/history", "显示当前会话的对话历史摘要")
    t1.add_row("/clear", "清除当前对话历史")
    t1.add_row("/save [路径]", "保存完整对话记录（含工具调用）到 JSON")
    t1.add_row("exit / quit / Ctrl+C", "退出程序")

    # ── 技能与工具 ──
    t2 = _section_table()
    t2.add_row("/skills", "查看已加载 Skillpacks 与路由结果")
    t2.add_row("/skills list", "列出全部 Skillpack 摘要")
    t2.add_row("/skills get <name>", "查看单个 Skillpack 详情")
    t2.add_row("/skills create/patch/delete", "管理 project Skillpack")
    t2.add_row("/<skill_name> [args...]", "手动调用 Skillpack")
    t2.add_row("/model [list|<name>]", "查看/切换模型（支持补全）")
    t2.add_row("/mcp", "查看 MCP Server 连接状态")
    t2.add_row("/config [list|set|get|delete]", "MCP 环境变量配置管理")

    # ── 会话控制 ──
    t3 = _section_table()
    t3.add_row("/subagent [on|off|status|list]", "会话级 subagent 开关与列表")
    t3.add_row("/subagent run [agent] -- <task>", "指定/自动选择 subagent 执行任务")
    t3.add_row("/fullaccess [on|off|status]", "会话级代码技能权限控制")
    t3.add_row("/backup [on|off|status|apply|list]", "备份沙盒模式控制")
    t3.add_row("/plan [on|off|status]", "plan mode 开关与状态")
    t3.add_row("/plan approve/reject [id]", "批准或拒绝待审批计划")

    # ── 审批操作 ──
    t4 = _section_table()
    t4.add_row("/accept <id>", "执行待确认高风险操作")
    t4.add_row("/reject <id>", "拒绝待确认高风险操作")
    t4.add_row("/undo <id>", "回滚已确认且可回滚的操作")
    t4.add_row("多选回答", "每行一个选项，空行提交")

    # ── 显示模式 ──
    t6 = _section_table()
    t6.add_row("/ui [status]", "查看当前布局模式（dashboard / classic）")
    t6.add_row("/ui dashboard", "切换到 Dashboard 密集信息模式")
    t6.add_row("/ui classic", "切换到经典流式输出模式")

    # 技能命令
    skill_rows = _load_skill_command_rows(engine) if engine is not None else []
    t5: Table | None = None
    if skill_rows:
        t5 = _section_table()
        for name, argument_hint in skill_rows:
            hint_text = argument_hint if argument_hint else "(无参数提示)"
            t5.add_row(f"/{name}", hint_text)

    # 组装渲染
    sections: list[tuple[str, Table | str]] = [
        ("💬 对话与导航", t1),
        ("🧩 技能与工具", t2),
        ("⚙️  会话控制", t3),
        ("🔐 审批操作", t4),
        ("🖥️  显示模式", t6),
    ]
    if t5:
        sections.append(("📦 已加载技能", t5))

    # 快速入门流程示例
    flow_example = (
        "  [dim white]典型使用步骤：[/dim white]\n"
        '  [dim white]1.[/dim white] 输入自然语言指令（如 "读取 sales.xlsx 前10行"）\n'
        "  [dim white]2.[/dim white] 查看工具调用过程与结果\n"
        "  [dim white]3.[/dim white] 高风险操作需 /accept 确认\n"
        "  [dim white]4.[/dim white] 使用 /ui dashboard 切换密集信息模式"
    )
    sections.append(("🚀 快速入门", flow_example))

    parts: list[str | Table] = []
    for i, (title, tbl) in enumerate(sections):
        if i > 0:
            parts.append("")
        parts.append(f"  [bold #5fd7af]{title}[/bold #5fd7af]")
        parts.append(tbl)

    from rich.console import Group

    console.print()
    console.print(
        Panel(
            Group(*parts),
            title="[bold]📖 帮助[/bold]",
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
    """渲染对话历史摘要 — 回合聚合视图。"""
    messages = engine.memory.get_messages()

    if not messages or all(m.get("role") == "system" for m in messages):
        console.print("  [dim white]暂无对话历史。[/dim white]")
        return

    # 按回合聚合：每个 user 消息开始一个新回合
    turns: list[dict] = []
    current_turn: dict | None = None
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")
        if role == "system":
            continue
        if role == "user" and content:
            current_turn = {
                "user_input": content,
                "assistant_reply": "",
                "tool_calls": [],
                "tool_results": [],
            }
            turns.append(current_turn)
        elif current_turn is not None:
            if role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "unknown")
                    current_turn["tool_calls"].append(name)
                if content:
                    current_turn["assistant_reply"] = content
            elif role == "tool":
                name = msg.get("name", "")
                if name:
                    current_turn["tool_results"].append(name)

    if not turns:
        console.print("  [dim white]暂无对话历史。[/dim white]")
        return

    total_tool_calls = sum(len(t["tool_calls"]) for t in turns)
    history_entries: list[str] = []

    for i, turn in enumerate(turns, start=1):
        user_text = turn["user_input"]
        display_user = user_text if len(user_text) <= 70 else user_text[:67] + "…"
        reply = turn["assistant_reply"]
        display_reply = reply if len(reply) <= 70 else reply[:67] + "…"
        tools = turn["tool_calls"]

        header = f"  [bold #5fd7af]回合 #{i}[/bold #5fd7af]"
        if tools:
            tool_names = ", ".join(dict.fromkeys(tools))
            header += f"  [dim white]🔧 {tool_names}[/dim white]"
        history_entries.append(header)
        history_entries.append(f"    [bold green]▸[/bold green] {display_user}")
        if display_reply:
            history_entries.append(f"    [bold #81a2be]◂[/bold #81a2be] {display_reply}")

    # 统计摘要
    stats_line = (
        f"  [dim white]{len(turns)} 个回合 · "
        f"{total_tool_calls} 次工具调用[/dim white]"
    )

    console.print()
    console.print(
        Panel(
            "\n".join(history_entries) + "\n\n" + stats_line,
            title=f"[bold]📋 对话历史[/bold]",
            title_align="left",
            border_style="#de935f",
            expand=False,
            padding=(1, 1),
        )
    )
    console.print()


def _handle_save_command(engine: AgentEngine, user_input: str) -> None:
    """处理 /save 命令：保存完整对话记录（含工具调用）到 JSON 文件。

    用法:
        /save              — 保存到 outputs/conversations/ 下自动命名
        /save <路径>       — 保存到指定路径
    """
    import uuid
    from datetime import datetime, timezone

    parts = user_input.split(maxsplit=1)
    output_path_str = parts[1].strip() if len(parts) > 1 else ""

    # 获取完整对话消息
    messages = engine.memory.get_messages()

    # 序列化消息（确保所有值可 JSON 化）
    serialized_messages: list[dict] = []
    for msg in messages:
        entry: dict = {}
        for key, value in msg.items():
            if value is None:
                entry[key] = None
            elif isinstance(value, (str, int, float, bool)):
                entry[key] = value
            elif isinstance(value, (list, dict)):
                entry[key] = value
            else:
                entry[key] = str(value)
        serialized_messages.append(entry)

    # 统计信息
    user_count = sum(1 for m in serialized_messages if m.get("role") == "user")
    assistant_count = sum(1 for m in serialized_messages if m.get("role") == "assistant")
    tool_msg_count = sum(1 for m in serialized_messages if m.get("role") == "tool")
    tool_call_count = sum(
        len(m.get("tool_calls") or [])
        for m in serialized_messages
        if m.get("tool_calls")
    )

    timestamp = datetime.now(timezone.utc).isoformat()
    model_name = getattr(engine, "current_model_name", None) or getattr(engine, "current_model", "unknown")

    save_data = {
        "schema_version": 2,
        "kind": "conversation_export",
        "timestamp": timestamp,
        "meta": {
            "active_model": model_name if isinstance(model_name, str) else str(model_name),
            "session_turn": getattr(engine, "_session_turn", 0),
        },
        "stats": {
            "message_count": len(serialized_messages),
            "user_messages": user_count,
            "assistant_messages": assistant_count,
            "tool_messages": tool_msg_count,
            "tool_call_count": tool_call_count,
        },
        "diagnostics": getattr(engine, "session_diagnostics", []),
        "messages": serialized_messages,
    }

    # 确定输出路径
    if output_path_str:
        filepath = Path(output_path_str)
    else:
        output_dir = Path("outputs") / "conversations"
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        short_id = uuid.uuid4().hex[:6]
        filepath = output_dir / f"conversation_{ts}_{short_id}.json"

    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    console.print(f"  [green]✓ 对话已保存到：{filepath}[/green]")
    console.print(
        f"  [dim white]共 {len(serialized_messages)} 条消息"
        f"（用户 {user_count} / 助手 {assistant_count}"
        f" / 工具调用 {tool_call_count} / 工具结果 {tool_msg_count}）[/dim white]"
    )


def _render_farewell() -> None:
    """渲染告别信息。"""
    farewell = Text()
    farewell.append("\n  ")
    farewell.append("─" * 40, style="dim #5f5f5f")
    farewell.append("\n  ")
    farewell.append("感谢使用 ", style="#81a2be")
    farewell.append("ExcelManus", style="bold #5fd7af")
    farewell.append("，再见！", style="#81a2be")
    farewell.append(" 👋")
    farewell.append("\n")
    console.print(farewell)


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
    tool_count = len(engine._all_tool_names()) if hasattr(engine, "_all_tool_names") else 0
    table.add_row("可用工具", f"{tool_count} 个工具")
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
    table.add_row(
        "备份模式",
        "enabled" if engine.backup_enabled else "disabled",
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


def _handle_ui_command(user_input: str, engine: "AgentEngine") -> bool:
    """处理 /ui 命令：查看/切换 CLI 显示模式。返回 True 表示已处理。"""
    global _current_layout_mode

    stripped = user_input.strip()
    lowered = stripped.lower()

    # /ui 或 /ui status — 显示当前模式
    if lowered in ("/ui", "/ui status"):
        console.print(
            f"  [dim white]当前布局模式：[/dim white][bold #5fd7af]{_current_layout_mode}[/bold #5fd7af]"
        )
        return True

    # /ui dashboard
    if lowered == "/ui dashboard":
        _current_layout_mode = "dashboard"
        console.print(
            "  [green]✓[/green] 已切换到 [bold #5fd7af]dashboard[/bold #5fd7af] 模式"
        )
        return True

    # /ui classic
    if lowered == "/ui classic":
        _current_layout_mode = "classic"
        console.print(
            "  [green]✓[/green] 已切换到 [bold #f0c674]classic[/bold #f0c674] 模式"
        )
        return True

    console.print(
        "  [#de935f]未知 /ui 子命令。可用：status / dashboard / classic[/#de935f]"
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
    """CLI 动态状态提示：在等待回复期间输出 spinner 动画。"""

    _FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, console: Console, *, enabled: bool, interval: float = 0.12) -> None:
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
        if event.event_type == EventType.SUBAGENT_START:
            name = (event.subagent_name or "").strip()
            self._status_label = f"子代理 {name}" if name else "调用子代理"
            return
        if event.event_type == EventType.SUBAGENT_ITERATION:
            name = (event.subagent_name or "").strip() or "subagent"
            turn = event.subagent_iterations or event.iteration or 0
            self._status_label = f"子代理 {name} 第 {turn} 轮"
            return
        if event.event_type == EventType.SUBAGENT_SUMMARY:
            self._status_label = "汇总子代理结果"
            return
        if event.event_type == EventType.SUBAGENT_END:
            self._status_label = "子代理收尾中"
            return
        if event.event_type == EventType.CHAT_SUMMARY:
            self._status_label = "整理结果"
            return
        if event.event_type in (EventType.TEXT_DELTA, EventType.THINKING_DELTA):
            self._status_label = ""
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
) -> tuple[str, bool]:
    """统一封装 chat 调用，增加等待期动态状态反馈。返回 (reply_text, streamed)。"""
    ticker = _LiveStatusTicker(console, enabled=_is_interactive_terminal())
    event_handler = ticker.wrap_handler(renderer.handle_event)

    await ticker.start()
    try:
        chat_kwargs: dict[str, object] = {"on_event": event_handler}
        if slash_command is not None:
            chat_kwargs["slash_command"] = slash_command
        if raw_args is not None:
            chat_kwargs["raw_args"] = raw_args
        reply = _reply_text(await engine.chat(user_input, **chat_kwargs))
        streamed = renderer._streaming_text or renderer._streaming_thinking
        renderer.finish_streaming()
        return reply, streamed
    finally:
        await ticker.stop()


async def _run_chat_turn(
    engine: AgentEngine,
    *,
    user_input: str,
    slash_command: str | None = None,
    raw_args: str | None = None,
    error_label: str = "处理请求",
) -> tuple[str, bool] | None:
    """统一回合执行入口：根据 _current_layout_mode 选择渲染器，调用引擎，渲染结果。

    返回 (reply_text, streamed)；异常时返回 None 并在终端输出错误。
    """
    try:
        if _current_layout_mode == "dashboard":
            from excelmanus.renderer_dashboard import DashboardRenderer
            renderer = DashboardRenderer(console)
            _turn = getattr(engine, "turn_count", 0)
            if callable(_turn):
                _turn = _turn()
            _turn = _turn if isinstance(_turn, int) else 0
            _model = getattr(engine, "current_model_name", None) or ""
            renderer.start_turn(
                turn_number=_turn if isinstance(_turn, int) else 0,
                model_name=_model if isinstance(_model, str) else "",
            )
        else:
            renderer = StreamRenderer(console)

        import time as _time
        _t0 = _time.monotonic()

        console.print()
        reply, streamed = await _chat_with_feedback(
            engine,
            user_input=user_input,
            renderer=renderer,
            slash_command=slash_command,
            raw_args=raw_args,
        )

        if not streamed:
            console.print()
            console.print(
                Panel(
                    Markdown(reply),
                    border_style="#5f875f",
                    padding=(1, 2),
                    expand=False,
                )
            )

        # Dashboard: 渲染回合 footer 摘要
        if _current_layout_mode == "dashboard" and hasattr(renderer, "finish_turn"):
            _elapsed = _time.monotonic() - _t0
            renderer.finish_turn(elapsed_seconds=_elapsed)

        return reply, streamed
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        logger.error("%s时发生错误: %s", error_label, exc, exc_info=True)
        # Dashboard: 渲染失败 footer
        if _current_layout_mode == "dashboard":
            try:
                from excelmanus.renderer_dashboard import DashboardRenderer as _DR
                if isinstance(renderer, _DR):
                    renderer.fail_turn(str(exc))
            except Exception:
                pass
        from excelmanus.cli_errors import render_error_panel
        render_error_panel(console, error=exc, error_label=error_label)
        return None


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
                            await _run_chat_turn(
                                engine,
                                user_input=user_input,
                                error_label="处理待回答问题",
                            )
                        except KeyboardInterrupt:
                            _render_farewell()
                            return
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
                        # 先开启 fullaccess，再 accept
                        user_input = f"/fullaccess on"
                    else:
                        user_input = f"/reject {pending_apv.approval_id}"

                    try:
                        await _run_chat_turn(
                            engine,
                            user_input=user_input,
                            error_label="处理审批操作",
                        )
                        # 全部授权模式：开启 fullaccess 后自动 accept
                        if approval_choice == _APPROVAL_OPTION_FULLACCESS:
                            await _run_chat_turn(
                                engine,
                                user_input=f"/accept {pending_apv.approval_id}",
                                error_label="处理审批操作",
                            )
                    except KeyboardInterrupt:
                        _render_farewell()
                        return
                    continue
                # approval_choice 为 None（不支持或 Esc），回退到普通输入

        try:
            if waiting_multiselect:
                console.print(
                    "  [dim white]多选回答模式：每行输入一个选项，空行提交。[/dim white]"
                )
                user_input = (await _read_multiline_user_input()).strip()
            else:
                _model_hint = getattr(engine, "current_model_name", None) or ""
                _turn = getattr(engine, "turn_count", 0)
                if callable(_turn):
                    _turn = _turn()
                _turn = _turn if isinstance(_turn, int) else 0
                _subagent_on = bool(getattr(engine, "subagent_enabled", False))
                _plan_on = bool(getattr(engine, "plan_mode", False))
                user_input = (await _read_user_input(
                    model_hint=_model_hint if isinstance(_model_hint, str) else "",
                    turn_number=_turn if isinstance(_turn, int) else 0,
                    subagent_active=_subagent_on,
                    plan_mode=_plan_on,
                )).strip()
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
                await _run_chat_turn(
                    engine,
                    user_input=user_input,
                    error_label="处理待回答问题",
                )
            except KeyboardInterrupt:
                _render_farewell()
                return
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

        if user_input.lower().startswith("/save"):
            try:
                _handle_save_command(engine, user_input)
            except Exception as exc:
                logger.error("处理 /save 命令失败: %s", exc, exc_info=True)
                console.print(f"  [red]✗ /save 命令执行失败：{exc}[/red]")
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

        if user_input.lower().startswith("/ui"):
            _handle_ui_command(user_input, engine)
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
            if lowered_cmd in _SUBAGENT_COMMAND_ALIASES:
                try:
                    await _run_chat_turn(
                        engine,
                        user_input=user_input,
                        error_label="处理子代理命令",
                    )
                except KeyboardInterrupt:
                    _render_farewell()
                    return
            else:
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
                await _run_chat_turn(
                    engine,
                    user_input=user_input,
                    slash_command=resolved_skill,
                    raw_args=raw_args,
                    error_label="处理技能命令",
                )
            except KeyboardInterrupt:
                _render_farewell()
                return
            continue

        # 未知斜杠命令提示（近似推荐 Top3）
        if user_input.startswith("/"):
            similar = _suggest_similar_commands(user_input)
            if similar:
                suggestion = ", ".join(similar)
                console.print(
                    f"  [#de935f]未知命令：{user_input}。你是否想输入：{suggestion}[/#de935f]"
                )
            else:
                console.print(
                    f"  [#de935f]未知命令：{user_input}。使用 /help 查看可用命令。[/#de935f]"
                )
            continue

        # 自然语言指令：调用 AgentEngine，使用事件流渲染
        try:
            await _run_chat_turn(
                engine,
                user_input=user_input,
                error_label="处理请求",
            )
        except KeyboardInterrupt:
            _render_farewell()
            return


async def _async_main() -> None:
    """异步入口：初始化组件并启动 REPL。"""
    import time as _time

    # ── 打印 Logo ──────────────────────────────────
    console.print()
    _render_gradient_logo()
    console.print(
        f"  v{__version__}  ·  基于大语言模型的 Excel 智能代理\n",
        style="dim white",
    )

    # ── 1. 加载配置 ─────────────────────────────────
    try:
        config = load_config()
    except ConfigError as exc:
        console.print(f"  [red]✗ 配置错误：{exc}[/red]")
        sys.exit(1)

    setup_logging(config.log_level)

    # 初始化布局模式
    global _current_layout_mode
    _current_layout_mode = config.cli_layout_mode
    console.print("  [green]✓[/green] [dim white]配置已加载[/dim white]", highlight=False)

    # ── 2. 注册内置工具 ─────────────────────
    registry = ToolRegistry()
    registry.register_builtin_tools(config.workspace_root)
    builtin_count = len(registry.get_tool_names())
    console.print(
        f"  [green]✓[/green] [dim white]内置工具[/dim white] [bold #5fd7af]{builtin_count}[/bold #5fd7af]",
        highlight=False,
    )

    # ── 3. 加载 Skillpacks ────────────────────
    loader = SkillpackLoader(config, registry)
    loader.load_all()
    router = SkillRouter(config, loader)
    skill_count = len(loader.list_skillpacks())
    console.print(
        f"  [green]✓[/green] [dim white]Skillpacks[/dim white] [bold #5fd7af]{skill_count}[/bold #5fd7af]",
        highlight=False,
    )

    # ── 4. 持久记忆 ─────────────────────────────────
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
        console.print(
            "  [green]✓[/green] [dim white]持久记忆[/dim white] [bold #5fd7af]已启用[/bold #5fd7af]",
            highlight=False,
        )
    else:
        console.print(
            "  [dim #5f5f5f]○ 持久记忆已禁用[/dim #5f5f5f]",
            highlight=False,
        )

    # ── 5. 创建引擎 ─────────────────────────────────
    engine = AgentEngine(
        config,
        registry,
        skill_router=router,
        persistent_memory=persistent_memory,
        memory_extractor=memory_extractor,
    )
    _sync_skill_command_suggestions(engine)
    _sync_model_suggestions(engine)

    # ── 6. MCP 连接（可能较慢，用 spinner）──────────
    mcp_count = 0
    mcp_tool_count = 0
    with console.status(
        "  [dim white]⟳ 正在连接 MCP Server…[/dim white]",
        spinner="dots",
        spinner_style="#5fd7af",
    ):
        t0 = _time.monotonic()
        try:
            await engine.initialize_mcp()
        except Exception:
            logger.warning("MCP 初始化失败，已跳过", exc_info=True)
        elapsed_ms = int((_time.monotonic() - t0) * 1000)

    mcp_count = engine.mcp_connected_count
    if mcp_count > 0:
        # 统计远程工具总数
        for info in engine._mcp_manager.get_server_info():
            if info.get("status") == "ready":
                mcp_tool_count += info.get("tool_count", 0)
        console.print(
            f"  [green]✓[/green] [dim white]MCP Server[/dim white] [bold #5fd7af]{mcp_count}[/bold #5fd7af]"
            f"  [dim #888888]({mcp_tool_count} 工具, {elapsed_ms}ms)[/dim #888888]",
            highlight=False,
        )
    else:
        console.print(
            f"  [dim #5f5f5f]○ 无 MCP Server[/dim #5f5f5f]  [dim #5f5f5f]({elapsed_ms}ms)[/dim #5f5f5f]",
            highlight=False,
        )

    # ── 启动信息面板 ────────────────────────────────
    console.print()  # 启动序列与面板之间留白
    if _AUTO_SAVE_PATH is not None:
        save_hint = _AUTO_SAVE_PATH if _AUTO_SAVE_PATH else "outputs/conversations/ (自动)"
        console.print(
            f"  [green]✓[/green] [dim white]对话自动保存[/dim white] [bold #5fd7af]{save_hint}[/bold #5fd7af]",
            highlight=False,
        )
    _render_welcome(config, skill_count, mcp_count)

    # 启动 REPL 循环
    try:
        await _repl_loop(engine)
    finally:
        # --save 自动保存
        if _AUTO_SAVE_PATH is not None:
            try:
                save_input = f"/save {_AUTO_SAVE_PATH}".strip() if _AUTO_SAVE_PATH else "/save"
                _handle_save_command(engine, save_input)
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

    parser = argparse.ArgumentParser(
        prog="excelmanus",
        description="ExcelManus — 基于大语言模型的 Excel 智能代理",
        add_help=False,  # 避免与 /help 冲突
    )
    parser.add_argument(
        "--save",
        metavar="PATH",
        nargs="?",
        const="",  # --save 不带路径时为空字符串，表示自动生成路径
        default=None,
        help="退出时自动保存对话记录到 JSON（不指定路径则自动生成）",
    )
    args, _unknown = parser.parse_known_args()

    global _AUTO_SAVE_PATH
    _AUTO_SAVE_PATH = args.save  # None=未启用, ""=自动路径, "xxx"=指定路径

    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        # 顶层捕获 Ctrl+C，确保优雅退出
        _render_farewell()
