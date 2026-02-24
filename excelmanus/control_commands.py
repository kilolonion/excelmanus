"""会话控制命令注册表。

作为 engine 与 CLI 的共享单一数据源（single source of truth）：
- engine: 判定哪些 slash 命令属于 control command
- CLI: 命令路由、帮助文案、补全参数
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ControlCommandSpec:
    """会话控制命令定义。"""

    command: str
    description: str
    aliases: tuple[str, ...] = ()
    arguments: tuple[str, ...] = ()
    help_label: str = ""
    include_in_suggestions: bool = True
    include_in_help: bool = True

    @property
    def all_aliases(self) -> tuple[str, ...]:
        return (self.command, *self.aliases)


def normalize_control_command(command: str) -> str:
    """统一 control command 归一化规则。"""
    return command.strip().lower().replace("_", "")


CONTROL_COMMAND_SPECS: tuple[ControlCommandSpec, ...] = (
    ControlCommandSpec(
        command="/model",
        description="查看/切换模型",
        arguments=("list",),
    ),
    ControlCommandSpec(
        command="/subagent",
        description="子代理控制",
        aliases=("/sub_agent",),
        arguments=("status", "on", "off", "list", "run"),
    ),
    ControlCommandSpec(
        command="/fullaccess",
        description="权限控制",
        aliases=("/full_access",),
        arguments=("status", "on", "off"),
    ),
    ControlCommandSpec(
        command="/backup",
        description="工作区事务控制（备份沙盒）",
        aliases=("/workspace",),
        arguments=("status", "on", "off", "apply", "list"),
    ),
    ControlCommandSpec(
        command="/plan",
        description="计划模式",
        arguments=("status", "on", "off", "approve", "reject"),
    ),
    ControlCommandSpec(
        command="/compact",
        description="上下文压缩控制",
        arguments=("status", "on", "off"),
    ),
    ControlCommandSpec(
        command="/manifest",
        description="工作区清单构建",
        arguments=("status", "build"),
    ),
    ControlCommandSpec(
        command="/accept",
        description="确认操作",
        help_label="/accept <id>",
    ),
    ControlCommandSpec(
        command="/reject",
        description="拒绝操作",
        help_label="/reject <id>",
    ),
    ControlCommandSpec(
        command="/undo",
        description="回滚操作",
        help_label="/undo [list|<id>]",
        arguments=("list",),
    ),
    ControlCommandSpec(
        command="/rollback",
        description="回退对话到指定轮次",
        help_label="/rollback [list|<N>]",
        arguments=("list",),
    ),
    ControlCommandSpec(
        command="/rules",
        description="自定义规则管理",
        help_label="/rules [add|delete|toggle|session]",
        arguments=("add", "delete", "toggle", "session"),
    ),
    ControlCommandSpec(
        command="/memory",
        description="持久记忆管理",
        help_label="/memory [<category>|delete|clear]",
        arguments=("file_pattern", "user_pref", "error_solution", "general", "delete", "clear"),
    ),
)


CONTROL_COMMAND_ALIASES: frozenset[str] = frozenset(
    alias for spec in CONTROL_COMMAND_SPECS for alias in spec.all_aliases
)

NORMALIZED_ALIAS_TO_CANONICAL_CONTROL_COMMAND: dict[str, str] = {
    normalize_control_command(alias): normalize_control_command(spec.command)
    for spec in CONTROL_COMMAND_SPECS
    for alias in spec.all_aliases
}

NORMALIZED_CONTROL_COMMANDS: frozenset[str] = frozenset(
    NORMALIZED_ALIAS_TO_CANONICAL_CONTROL_COMMAND.values()
)

CONTROL_COMMAND_ARGUMENTS_BY_ALIAS: dict[str, tuple[str, ...]] = {
    alias: spec.arguments
    for spec in CONTROL_COMMAND_SPECS
    if spec.arguments
    for alias in spec.all_aliases
}

CONTROL_COMMAND_HELP_ENTRIES: tuple[tuple[str, str], ...] = tuple(
    (spec.help_label or spec.command, spec.description)
    for spec in CONTROL_COMMAND_SPECS
    if spec.include_in_help
)
