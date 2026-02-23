"""CLI 斜杠命令定义与处理 — 命令常量、分发、相似度建议。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from excelmanus.cli.theme import THEME
from excelmanus.control_commands import CONTROL_COMMAND_SPECS

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 命令常量
# ------------------------------------------------------------------

EXIT_COMMANDS = {"exit", "quit"}

CONFIG_ARGUMENTS = ("list", "set", "get", "delete", "export", "import")
SHORTCUT_ACTION_SHOW_HELP = "show_help"


@dataclass(frozen=True, slots=True)
class ShortcutSpec:
    """CLI 快捷提示定义（展示 + 可选输入触发动作）。"""

    label: str
    hint: str
    triggers: tuple[str, ...] = ()
    action: str | None = None


@dataclass(frozen=True, slots=True)
class StaticSlashCommand:
    """内置斜杠命令定义。"""

    command: str
    description: str
    aliases: tuple[str, ...] = ()
    arguments: tuple[str, ...] = ()
    session_control: bool = False
    include_in_suggestions: bool = True
    include_in_help: bool = True
    help_label: str = ""

    @property
    def all_aliases(self) -> tuple[str, ...]:
        return (self.command, *self.aliases)


_SHORTCUT_SPECS: tuple[ShortcutSpec, ...] = (
    ShortcutSpec("/ for commands", "exit to quit"),
    ShortcutSpec("@ for mentions", "shift+tab auto-accept"),
    ShortcutSpec(
        "? for shortcuts",
        "ctrl+c to exit",
        triggers=("?", "？"),
        action=SHORTCUT_ACTION_SHOW_HELP,
    ),
)

HELP_SHORTCUT_ENTRIES: tuple[tuple[str, str], ...] = tuple(
    (spec.label, spec.hint)
    for spec in _SHORTCUT_SPECS
)

_SHORTCUT_ACTION_BY_TRIGGER: dict[str, str] = {
    trigger: spec.action
    for spec in _SHORTCUT_SPECS
    if isinstance(spec.action, str)
    for trigger in spec.triggers
}


@dataclass(frozen=True, slots=True)
class PromptCommandSyncPayload:
    """同步到 prompt 模块的命令面快照。"""

    slash_command_suggestions: tuple[str, ...]
    dynamic_skill_slash_commands: tuple[str, ...]
    command_argument_map: dict[str, tuple[str, ...]]


_BASE_SLASH_COMMANDS: tuple[StaticSlashCommand, ...] = (
    StaticSlashCommand("/help", "显示帮助"),
    StaticSlashCommand("/skills", "查看技能包"),
    StaticSlashCommand("/history", "对话历史摘要"),
    StaticSlashCommand("/clear", "清除对话历史"),
    StaticSlashCommand("/mcp", "MCP Server 状态"),
    StaticSlashCommand("/save", "保存对话记录", help_label="/save [路径]"),
    StaticSlashCommand("/config", "环境变量配置", arguments=CONFIG_ARGUMENTS),
)

_CONTROL_SLASH_COMMANDS: tuple[StaticSlashCommand, ...] = tuple(
    StaticSlashCommand(
        command=spec.command,
        description=spec.description,
        aliases=spec.aliases,
        arguments=spec.arguments,
        session_control=True,
        include_in_suggestions=spec.include_in_suggestions,
        include_in_help=spec.include_in_help,
        help_label=spec.help_label,
    )
    for spec in CONTROL_COMMAND_SPECS
)

_STATIC_SLASH_COMMANDS: tuple[StaticSlashCommand, ...] = (
    *_BASE_SLASH_COMMANDS,
    *_CONTROL_SLASH_COMMANDS,
)


def _aliases_for(command: str) -> set[str]:
    for spec in _STATIC_SLASH_COMMANDS:
        if spec.command == command:
            return set(spec.all_aliases)
    raise ValueError(f"未知命令定义: {command}")


DECLARED_SLASH_COMMAND_ALIASES: tuple[str, ...] = tuple(
    dict.fromkeys(
        alias
        for spec in _STATIC_SLASH_COMMANDS
        for alias in spec.all_aliases
    )
)

# 兼容旧调用方：保留 membership 语义。
SLASH_COMMANDS = frozenset(DECLARED_SLASH_COMMAND_ALIASES)

FULL_ACCESS_ALIASES = _aliases_for("/fullaccess")
BACKUP_ALIASES = _aliases_for("/backup")
SUBAGENT_ALIASES = _aliases_for("/subagent")
APPROVAL_ALIASES = (
    _aliases_for("/accept")
    | _aliases_for("/reject")
    | _aliases_for("/undo")
)
PLAN_ALIASES = _aliases_for("/plan")
MODEL_ALIASES = _aliases_for("/model")
COMPACT_ALIASES = _aliases_for("/compact")
CONFIG_ALIASES = _aliases_for("/config")

SESSION_CONTROL_ALIASES = {
    alias
    for spec in _STATIC_SLASH_COMMANDS
    if spec.session_control
    for alias in spec.all_aliases
}

# 补全建议
SLASH_COMMAND_SUGGESTIONS = tuple(
    dict.fromkeys(
        alias
        for spec in _STATIC_SLASH_COMMANDS
        if spec.include_in_suggestions
        for alias in spec.all_aliases
    )
)

HELP_COMMAND_ENTRIES = tuple(
    (spec.help_label or spec.command, spec.description)
    for spec in _STATIC_SLASH_COMMANDS
    if spec.include_in_help
)

COMMAND_ARGUMENTS_BY_ALIAS: dict[str, tuple[str, ...]] = {
    alias: spec.arguments
    for spec in _STATIC_SLASH_COMMANDS
    if spec.arguments
    for alias in spec.all_aliases
}


def _normalize_model_names(model_names: object) -> tuple[str, ...]:
    """规范化模型名称列表（去重、去空、保序）。"""
    if not isinstance(model_names, (list, tuple, set)):
        return ()
    ordered: list[str] = []
    seen: set[str] = set()
    for item in model_names:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return tuple(ordered)


def build_command_argument_map(*, model_names: object = ()) -> dict[str, tuple[str, ...]]:
    """构建完整命令参数映射（含 /model 动态模型名）。"""
    arg_map = dict(COMMAND_ARGUMENTS_BY_ALIAS)
    normalized_model_names = _normalize_model_names(model_names)
    model_args = ("list", *normalized_model_names)
    for alias in MODEL_ALIASES:
        arg_map[alias] = model_args
    return arg_map


def build_prompt_command_sync_payload(engine: "AgentEngine") -> PromptCommandSyncPayload:
    """构建 prompt 所需的命令补全/参数同步载荷。"""
    rows = load_skill_command_rows(engine)
    dynamic_skill_slash_commands = tuple(
        dict.fromkeys(
            f"/{name.strip()}"
            for name, _ in rows
            if isinstance(name, str) and name.strip()
        )
    )

    model_names_getter = getattr(engine, "model_names", None)
    model_names = model_names_getter() if callable(model_names_getter) else ()

    return PromptCommandSyncPayload(
        slash_command_suggestions=SLASH_COMMAND_SUGGESTIONS,
        dynamic_skill_slash_commands=dynamic_skill_slash_commands,
        command_argument_map=build_command_argument_map(model_names=model_names),
    )


def resolve_shortcut_action(user_input: str) -> str | None:
    """解析输入是否命中已注册 shortcut 动作。"""
    return _SHORTCUT_ACTION_BY_TRIGGER.get(user_input.strip())


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------


def resolve_skill_slash_command(engine: "AgentEngine", user_input: str) -> str | None:
    """识别是否为可手动调用的 Skill 斜杠命令。"""
    resolver = getattr(engine, "resolve_skill_command", None)
    if not callable(resolver):
        return None
    resolved = resolver(user_input)
    if isinstance(resolved, str) and resolved.strip():
        return resolved.strip()
    return None


def extract_slash_raw_args(user_input: str) -> str:
    """提取 '/command ...' 中的参数字符串。"""
    if not user_input.startswith("/"):
        return ""
    _, _, raw_args = user_input[1:].partition(" ")
    return raw_args.strip()


_IMG_PATTERN = re.compile(r"@img\s+(\S+\.(?:png|jpg|jpeg|gif|bmp|webp))", re.IGNORECASE)


def parse_image_attachments(user_input: str) -> tuple[str, list[str]]:
    """解析 @img 语法，返回 (剩余文本, 图片路径列表)。"""
    images = _IMG_PATTERN.findall(user_input)
    text = _IMG_PATTERN.sub("", user_input).strip()
    return text, images


# ------------------------------------------------------------------
# 命令相似度推荐
# ------------------------------------------------------------------


def suggest_similar_commands(
    user_input: str,
    known_commands: tuple[str, ...] | None = None,
    *,
    max_results: int = 3,
) -> list[str]:
    """基于编辑距离返回最相似的已知命令。"""
    cmd = user_input.lower().split()[0] if user_input.strip() else ""
    if not cmd:
        return []
    if known_commands is None:
        known_commands = SLASH_COMMAND_SUGGESTIONS
    scored: list[tuple[float, str]] = []
    for candidate in known_commands:
        score = _command_similarity(cmd, candidate.lower())
        if score > 0:
            scored.append((score, candidate))
    scored.sort(key=lambda x: -x[0])
    return [s[1] for s in scored[:max_results]]


def _command_similarity(a: str, b: str) -> float:
    """计算两个命令字符串的相似度分数（0~1）。"""
    if a == b:
        return 1.0
    prefix_len = 0
    for ca, cb in zip(a, b):
        if ca == cb:
            prefix_len += 1
        else:
            break
    prefix_score = prefix_len / max(len(a), len(b)) if max(len(a), len(b)) > 0 else 0
    dist = _edit_distance(a, b)
    max_len = max(len(a), len(b))
    edit_score = 1.0 - (dist / max_len) if max_len > 0 else 0
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


# ------------------------------------------------------------------
# Skill 命令辅助
# ------------------------------------------------------------------


def load_skill_command_rows(engine: "AgentEngine") -> list[tuple[str, str]]:
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


def to_standard_skill_detail(detail: dict) -> dict:
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


def parse_skills_payload_options(tokens: list[str], start_idx: int) -> dict:
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


# ------------------------------------------------------------------
# /skills 子命令处理
# ------------------------------------------------------------------


def handle_skills_subcommand(
    console: Console,
    engine: "AgentEngine",
    user_input: str,
    *,
    sync_callback: Any = None,
) -> bool:
    """处理 `/skills ...` 子命令。返回是否已处理。"""
    if not user_input.startswith("/skills "):
        return False
    try:
        tokens = shlex.split(user_input)
    except ValueError as exc:
        console.print(f"  [{THEME.RED}]{THEME.FAILURE} 命令解析失败：{exc}[/{THEME.RED}]")
        return True

    if len(tokens) < 2:
        return False

    sub = tokens[1].lower()
    if sub == "list":
        rows = engine.list_skillpacks_detail()
        if not rows:
            console.print(f"  [{THEME.DIM}]当前没有已加载的 Skillpack。[/{THEME.DIM}]")
            return True
        table = Table(show_header=True, show_edge=False, pad_edge=False, expand=False)
        table.add_column("name", style=THEME.PRIMARY_LIGHT)
        table.add_column("source", style=THEME.CYAN)
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
            console.print(f"  [{THEME.GOLD}]用法：/skills get <name>[/{THEME.GOLD}]")
            return True
        name = tokens[2]
        detail = engine.get_skillpack_detail(name)
        detail = to_standard_skill_detail(detail)
        console.print(json.dumps(detail, ensure_ascii=False, indent=2))
        return True

    if sub == "create":
        if len(tokens) < 5:
            console.print(
                f"  [{THEME.GOLD}]用法：/skills create <name> --json '<payload>' "
                f"或 --json-file <path>[/{THEME.GOLD}]"
            )
            return True
        name = tokens[2]
        payload = parse_skills_payload_options(tokens, 3)
        detail = engine.create_skillpack(name, payload, actor="cli")
        detail = to_standard_skill_detail(detail)
        if sync_callback:
            sync_callback()
        console.print(
            json.dumps(
                {"status": "created", "name": detail.get("name"), "detail": detail},
                ensure_ascii=False, indent=2,
            )
        )
        return True

    if sub == "patch":
        if len(tokens) < 5:
            console.print(
                f"  [{THEME.GOLD}]用法：/skills patch <name> --json '<payload>' "
                f"或 --json-file <path>[/{THEME.GOLD}]"
            )
            return True
        name = tokens[2]
        payload = parse_skills_payload_options(tokens, 3)
        detail = engine.patch_skillpack(name, payload, actor="cli")
        detail = to_standard_skill_detail(detail)
        if sync_callback:
            sync_callback()
        console.print(
            json.dumps(
                {"status": "updated", "name": detail.get("name"), "detail": detail},
                ensure_ascii=False, indent=2,
            )
        )
        return True

    if sub == "delete":
        if len(tokens) < 3:
            console.print(f"  [{THEME.GOLD}]用法：/skills delete <name>[/{THEME.GOLD}]")
            return True
        name = tokens[2]
        try:
            engine.delete_skillpack(name, actor="cli")
            if sync_callback:
                sync_callback()
            console.print(f"  [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS} 已删除 Skillpack: {name}[/{THEME.PRIMARY_LIGHT}]")
        except Exception as exc:
            console.print(f"  [{THEME.RED}]{THEME.FAILURE} 删除失败：{exc}[/{THEME.RED}]")
        return True

    if sub == "import":
        return _handle_skills_import(
            console, engine, tokens,
            sync_callback=sync_callback,
        )

    console.print(
        f"  [{THEME.GOLD}]未知 /skills 子命令。可用：list/get/create/patch/delete/import[/{THEME.GOLD}]"
    )
    return True


def _handle_skills_import(
    console: Console,
    engine: "AgentEngine",
    tokens: list[str],
    *,
    sync_callback: Any = None,
) -> bool:
    """处理 `/skills import <path> [--url] [--overwrite]`。"""
    if len(tokens) < 3:
        console.print(
            f"  [{THEME.GOLD}]用法：\n"
            f"  /skills import /path/to/SKILL.md          本地文件导入\n"
            f"  /skills import --url <github-url>         GitHub URL 导入\n"
            f"  追加 --overwrite 可覆盖已存在的同名技能[/{THEME.GOLD}]"
        )
        return True

    overwrite = "--overwrite" in tokens
    remaining = [t for t in tokens[2:] if t != "--overwrite"]

    source: str
    value: str

    if "--url" in remaining:
        idx = remaining.index("--url")
        if idx + 1 >= len(remaining):
            console.print(f"  [{THEME.RED}]{THEME.FAILURE} --url 后需要提供 GitHub URL[/{THEME.RED}]")
            return True
        source = "github_url"
        value = remaining[idx + 1]
    else:
        if not remaining:
            console.print(f"  [{THEME.RED}]{THEME.FAILURE} 请提供 SKILL.md 文件路径或 --url <github-url>[/{THEME.RED}]")
            return True
        source = "local_path"
        value = remaining[0]

    try:
        result = engine.import_skillpack(
            source=source, value=value, actor="cli", overwrite=overwrite,
        )
        if sync_callback:
            sync_callback()
        name = result.get("name", "")
        files = result.get("files_copied", [])
        console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS} 已导入 Skillpack: {name}"
            f"（{len(files)} 个文件）[/{THEME.PRIMARY_LIGHT}]"
        )
        for f in files:
            console.print(f"    [{THEME.DIM}]• {f}[/{THEME.DIM}]")
    except Exception as exc:
        console.print(f"  [{THEME.RED}]{THEME.FAILURE} 导入失败：{exc}[/{THEME.RED}]")

    return True


# ------------------------------------------------------------------
# /config 命令
# ------------------------------------------------------------------

_CONFIG_ENV_REF_PATTERN = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def scan_mcp_env_vars(workspace_root: str = ".") -> list[str]:
    """扫描 mcp.json 中引用的所有 $VAR 环境变量名。"""
    from excelmanus.mcp.config import MCPConfigLoader  # noqa: F401

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


def mask_secret(value: str) -> str:
    """对敏感值脱敏。"""
    if len(value) <= 12:
        return value[:3] + "****" + value[-2:] if len(value) > 5 else "****"
    return value[:4] + "****" + value[-4:]


def dotenv_path(workspace_root: str = ".") -> Path:
    """返回工作区 .env 文件路径。"""
    return Path(workspace_root).resolve() / ".env"


def dotenv_set(dotenv_file: Path, key: str, value: str) -> None:
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
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(new_line)
    _write_dotenv_lines(dotenv_file, lines)
    os.environ[key] = value


def dotenv_delete(dotenv_file: Path, key: str) -> bool:
    """从 .env 文件中删除一个键。"""
    lines = _read_dotenv_lines(dotenv_file)
    pattern = re.compile(rf"^{re.escape(key)}\s*=")
    new_lines = [line for line in lines if not pattern.match(line)]
    if len(new_lines) == len(lines):
        return False
    _write_dotenv_lines(dotenv_file, new_lines)
    os.environ.pop(key, None)
    return True


def _read_dotenv_lines(dotenv_file: Path) -> list[str]:
    if not dotenv_file.is_file():
        return []
    return dotenv_file.read_text(encoding="utf-8").splitlines()


def _write_dotenv_lines(dotenv_file: Path, lines: list[str]) -> None:
    dotenv_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def handle_config_command(
    console: Console,
    user_input: str,
    workspace_root: str = ".",
) -> bool:
    """处理 /config 命令。"""
    stripped = user_input.strip()
    lowered = stripped.lower()

    if lowered in ("/config", "/config list"):
        env_vars = scan_mcp_env_vars(workspace_root)
        if not env_vars:
            console.print(f"  [{THEME.DIM}]mcp.json 中未发现环境变量引用。[/{THEME.DIM}]")
            return True

        table = Table(show_header=True, show_edge=False, pad_edge=False, expand=False)
        table.add_column("变量名", style=THEME.PRIMARY_LIGHT, min_width=20)
        table.add_column("状态", style=THEME.CYAN, min_width=8)
        table.add_column("值（脱敏）")

        for var_name in env_vars:
            value = os.environ.get(var_name)
            if value:
                table.add_row(var_name, f"[{THEME.PRIMARY_LIGHT}]已设置[/{THEME.PRIMARY_LIGHT}]", mask_secret(value))
            else:
                table.add_row(var_name, f"[{THEME.RED}]未设置[/{THEME.RED}]", "-")

        console.print()
        console.print(table)
        console.print(
            f"  [{THEME.DIM}]使用 /config set <KEY> <VALUE> 设置，"
            f"/config delete <KEY> 删除[/{THEME.DIM}]"
        )
        return True

    if lowered.startswith("/config set "):
        parts = stripped.split(None, 3)
        if len(parts) < 4:
            console.print(f"  [{THEME.GOLD}]用法：/config set <KEY> <VALUE>[/{THEME.GOLD}]")
            return True
        key = parts[2]
        value = parts[3]
        df = dotenv_path(workspace_root)
        try:
            dotenv_set(df, key, value)
            console.print(
                f"  [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS}[/{THEME.PRIMARY_LIGHT}]"
                f" 已设置 [{THEME.CYAN}]{key}[/{THEME.CYAN}] = {mask_secret(value)}"
            )
            console.print(f"  [{THEME.DIM}]已写入 {df}[/{THEME.DIM}]")
        except Exception as exc:
            console.print(f"  [{THEME.RED}]{THEME.FAILURE} 设置失败：{exc}[/{THEME.RED}]")
        return True

    if lowered.startswith("/config delete "):
        parts = stripped.split(None, 2)
        if len(parts) < 3:
            console.print(f"  [{THEME.GOLD}]用法：/config delete <KEY>[/{THEME.GOLD}]")
            return True
        key = parts[2]
        df = dotenv_path(workspace_root)
        if dotenv_delete(df, key):
            console.print(
                f"  [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS}[/{THEME.PRIMARY_LIGHT}]"
                f" 已删除 [{THEME.CYAN}]{key}[/{THEME.CYAN}]"
            )
        else:
            console.print(f"  [{THEME.GOLD}]未找到变量 {key}[/{THEME.GOLD}]")
        return True

    if lowered.startswith("/config get "):
        parts = stripped.split(None, 2)
        if len(parts) < 3:
            console.print(f"  [{THEME.GOLD}]用法：/config get <KEY>[/{THEME.GOLD}]")
            return True
        key = parts[2]
        value = os.environ.get(key)
        if value is not None:
            console.print(
                f"  [{THEME.PRIMARY_LIGHT}]{key}[/{THEME.PRIMARY_LIGHT}]"
                f" = {mask_secret(value)}"
            )
        else:
            console.print(f"  [{THEME.GOLD}]未找到变量 {key}[/{THEME.GOLD}]")
        return True

    if lowered.startswith("/config export"):
        return _handle_config_export(console, stripped, workspace_root)

    if lowered.startswith("/config import"):
        return _handle_config_import(console, stripped, workspace_root)

    console.print(f"  [{THEME.GOLD}]未知 /config 子命令。可用：list/set/get/delete/export/import[/{THEME.GOLD}]")
    return True


# ------------------------------------------------------------------
# /config export / import
# ------------------------------------------------------------------


def _handle_config_export(
    console: Console,
    user_input: str,
    workspace_root: str = ".",
) -> bool:
    """处理 /config export [--simple] [--sections main,aux,vlm,profiles]。"""
    from getpass import getpass

    from excelmanus.config import load_config
    from excelmanus.config_transfer import export_config

    parts = user_input.split()
    mode = "password"
    section_names = ["main", "aux", "vlm", "profiles"]

    idx = 2  # skip "/config export"
    while idx < len(parts):
        if parts[idx] == "--simple":
            mode = "simple"
        elif parts[idx] == "--sections" and idx + 1 < len(parts):
            idx += 1
            section_names = [s.strip() for s in parts[idx].split(",") if s.strip()]
        idx += 1

    try:
        cfg = load_config()
    except Exception as exc:
        console.print(f"  [{THEME.RED}]{THEME.FAILURE} 加载配置失败：{exc}[/{THEME.RED}]")
        return True

    sections: dict = {}
    if "main" in section_names:
        sections["main"] = {"api_key": cfg.api_key, "base_url": cfg.base_url, "model": cfg.model}
    if "aux" in section_names:
        sections["aux"] = {"api_key": cfg.aux_api_key or "", "base_url": cfg.aux_base_url or "", "model": cfg.aux_model or ""}
    if "vlm" in section_names:
        sections["vlm"] = {"api_key": cfg.vlm_api_key or "", "base_url": cfg.vlm_base_url or "", "model": cfg.vlm_model or ""}
    if "profiles" in section_names:
        profiles = [
            {"name": p.name, "model": p.model, "api_key": p.api_key, "base_url": p.base_url, "description": p.description}
            for p in cfg.models
        ]
        sections["profiles"] = profiles

    password: str | None = None
    if mode == "password":
        try:
            password = getpass("  设置加密密码: ")
            confirm = getpass("  确认密码: ")
        except (EOFError, KeyboardInterrupt):
            console.print(f"\n  [{THEME.DIM}]已取消。[/{THEME.DIM}]")
            return True
        if password != confirm:
            console.print(f"  [{THEME.RED}]{THEME.FAILURE} 两次密码不一致。[/{THEME.RED}]")
            return True
        if not password:
            console.print(f"  [{THEME.RED}]{THEME.FAILURE} 密码不能为空。[/{THEME.RED}]")
            return True

    try:
        token = export_config(sections, password=password, mode=mode)
    except Exception as exc:
        console.print(f"  [{THEME.RED}]{THEME.FAILURE} 导出失败：{exc}[/{THEME.RED}]")
        return True

    console.print()
    console.print(f"  [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS} 配置已导出（{mode} 模式）[/{THEME.PRIMARY_LIGHT}]")
    console.print(f"  [{THEME.DIM}]包含区块：{', '.join(sections.keys())}[/{THEME.DIM}]")
    console.print()
    console.print(f"  [{THEME.CYAN}]{token}[/{THEME.CYAN}]")
    console.print()
    if mode == "password":
        console.print(f"  [{THEME.GOLD}]请将此令牌和密码一起发送给接收方。[/{THEME.GOLD}]")
    else:
        console.print(f"  [{THEME.GOLD}]简单分享模式：令牌本身即可导入，无需密码。[/{THEME.GOLD}]")
    console.print()
    return True


def _handle_config_import(
    console: Console,
    user_input: str,
    workspace_root: str = ".",
) -> bool:
    """处理 /config import <token> [--password <pw>]。"""
    from getpass import getpass

    from excelmanus.config_transfer import detect_token_mode, import_config

    parts = user_input.split(None, 2)  # "/config import <token>"
    if len(parts) < 3:
        console.print(f"  [{THEME.GOLD}]用法：/config import <令牌字符串>[/{THEME.GOLD}]")
        return True

    token = parts[2].strip()

    mode = detect_token_mode(token)
    if mode is None:
        console.print(f"  [{THEME.RED}]{THEME.FAILURE} 无效的令牌格式。[/{THEME.RED}]")
        return True

    password: str | None = None
    if mode == "password":
        try:
            password = getpass("  输入解密密码: ")
        except (EOFError, KeyboardInterrupt):
            console.print(f"\n  [{THEME.DIM}]已取消。[/{THEME.DIM}]")
            return True

    try:
        payload = import_config(token, password=password)
    except ValueError as exc:
        console.print(f"  [{THEME.RED}]{THEME.FAILURE} 导入失败：{exc}[/{THEME.RED}]")
        return True

    sections = payload.get("sections", {})
    exported_at = payload.get("ts", "")

    df = dotenv_path(workspace_root)
    imported_items: list[str] = []

    _ENV_KEY_MAP = {
        "main": {"api_key": "EXCELMANUS_API_KEY", "base_url": "EXCELMANUS_BASE_URL", "model": "EXCELMANUS_MODEL"},
        "aux": {"api_key": "EXCELMANUS_AUX_API_KEY", "base_url": "EXCELMANUS_AUX_BASE_URL", "model": "EXCELMANUS_AUX_MODEL"},
        "vlm": {"api_key": "EXCELMANUS_VLM_API_KEY", "base_url": "EXCELMANUS_VLM_BASE_URL", "model": "EXCELMANUS_VLM_MODEL"},
    }

    for section_key in ("main", "aux", "vlm"):
        data = sections.get(section_key)
        if not isinstance(data, dict):
            continue
        key_map = _ENV_KEY_MAP.get(section_key, {})
        for field in ("api_key", "base_url", "model"):
            val = data.get(field)
            if val and isinstance(val, str) and field in key_map:
                dotenv_set(df, key_map[field], val)
        imported_items.append(section_key)

    profiles = sections.get("profiles")
    if isinstance(profiles, list) and profiles:
        imported_items.append(f"profiles({len(profiles)})")

    console.print()
    console.print(f"  [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS} 配置已导入[/{THEME.PRIMARY_LIGHT}]")
    if exported_at:
        console.print(f"  [{THEME.DIM}]导出时间：{exported_at}[/{THEME.DIM}]")
    console.print(f"  [{THEME.DIM}]已导入区块：{', '.join(imported_items)}[/{THEME.DIM}]")
    if isinstance(profiles, list) and profiles:
        console.print(f"  [{THEME.GOLD}]注意：多模型 profiles 需重启后生效（或通过 API 导入）。[/{THEME.GOLD}]")
    console.print(f"  [{THEME.DIM}]已写入 {df}[/{THEME.DIM}]")
    console.print()
    return True


# ------------------------------------------------------------------
# /history, /skills, /mcp 渲染
# ------------------------------------------------------------------


def render_history(console: Console, engine: "AgentEngine") -> None:
    """渲染对话历史摘要。"""
    from excelmanus.cli.utils import separator_line
    history_fn = getattr(engine, "conversation_summary", None)
    if not callable(history_fn):
        console.print(f"  [{THEME.DIM}]对话历史功能不可用。[/{THEME.DIM}]")
        return
    summary = history_fn()
    if not summary:
        console.print(f"  [{THEME.DIM}]暂无对话记录。[/{THEME.DIM}]")
        return
    sep = separator_line(50)
    console.print()
    console.print(f"  [{THEME.DIM}]{sep}[/{THEME.DIM}]")
    console.print(f"  {summary}")
    console.print(f"  [{THEME.DIM}]{sep}[/{THEME.DIM}]")
    console.print()


def render_skills(console: Console, engine: "AgentEngine") -> None:
    """渲染技能包列表。"""
    rows = load_skill_command_rows(engine)
    if not rows:
        console.print(f"  [{THEME.DIM}]当前没有已加载的技能包。[/{THEME.DIM}]")
        return
    console.print()
    for name, hint in rows:
        hint_text = f" [{THEME.DIM}]{hint}[/{THEME.DIM}]" if hint else ""
        console.print(f"  [{THEME.PRIMARY_LIGHT}]/{name}[/{THEME.PRIMARY_LIGHT}]{hint_text}")
    console.print()


def render_mcp(console: Console, engine: "AgentEngine") -> None:
    """渲染 MCP Server 状态。"""
    mcp_manager = getattr(engine, "_mcp_manager", None)
    if mcp_manager is None:
        console.print(f"  [{THEME.DIM}]MCP Server 管理器不可用。[/{THEME.DIM}]")
        return
    status_fn = getattr(mcp_manager, "server_status_rows", None)
    if not callable(status_fn):
        console.print(f"  [{THEME.DIM}]MCP Server 状态查询不可用。[/{THEME.DIM}]")
        return
    rows = status_fn()
    if not rows:
        console.print(f"  [{THEME.DIM}]无 MCP Server 配置。[/{THEME.DIM}]")
        return

    table = Table(show_header=True, show_edge=False, pad_edge=False, expand=False)
    table.add_column("Server", style=THEME.PRIMARY_LIGHT)
    table.add_column("状态", style=THEME.CYAN)
    table.add_column("传输")
    table.add_column("工具数")
    table.add_column("错误", style=THEME.RED)

    for srv in rows:
        status = f"[{THEME.PRIMARY_LIGHT}]运行中[/{THEME.PRIMARY_LIGHT}]" if srv.get("running") else f"[{THEME.RED}]已停止[/{THEME.RED}]"
        last_error = srv.get("last_error", "") or ""
        table.add_row(
            srv["name"],
            status,
            srv.get("transport", "?"),
            str(srv.get("tool_count", 0)),
            last_error,
        )

    console.print()
    console.print(table)
    console.print()


def render_farewell(console: Console) -> None:
    """渲染退出消息。"""
    console.print()
    console.print(f"  [{THEME.DIM}]Goodbye![/{THEME.DIM}]")
    console.print()
