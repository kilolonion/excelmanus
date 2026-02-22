"""Hook 处理器。"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from typing import Any

from excelmanus.config import ExcelManusConfig
from excelmanus.hooks.models import HookAgentAction, HookDecision, HookResult

_SHELL_CONTROL_SPLIT_PATTERN = re.compile(r"(?:&&|\|\||;|\||\n)")
_SHELL_METACHAR_PATTERN = re.compile(r"\$\(|\$\{|`")


def _parse_decision(value: Any) -> HookDecision:
    if not isinstance(value, str):
        return HookDecision.CONTINUE
    normalized = value.strip().lower()
    for item in HookDecision:
        if normalized == item.value:
            return item
    return HookDecision.CONTINUE


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _build_result_from_mapping(data: dict[str, Any]) -> HookResult:
    hook_specific = _to_dict(data.get("hookSpecificOutput"))

    decision = _parse_decision(
        hook_specific.get("permissionDecision", data.get("decision"))
    )
    reason = str(
        hook_specific.get("permissionDecisionReason", data.get("reason", "")) or ""
    )
    updated_input = hook_specific.get("updatedInput", data.get("updated_input"))
    if updated_input is not None and not isinstance(updated_input, dict):
        updated_input = None

    additional_context = str(
        hook_specific.get("additionalContext", data.get("additional_context", ""))
        or ""
    )
    return HookResult(
        decision=decision,
        reason=reason,
        updated_input=updated_input,
        additional_context=additional_context,
        raw_output=data,
    )


def _first_command_segment(command: str) -> str:
    """提取 shell 命令首段，避免 allowlist 被命令分隔符绕过。"""
    segment = _SHELL_CONTROL_SPLIT_PATTERN.split(command, maxsplit=1)[0]
    return segment.strip()


def _matches_prefix_with_boundary(text: str, prefix: str) -> bool:
    if not text.startswith(prefix):
        return False
    if len(text) == len(prefix):
        return True
    return text[len(prefix)].isspace()


def _allowlist_matches_command(*, command: str, allowlist: tuple[str, ...]) -> bool:
    normalized = command.strip()
    if _SHELL_METACHAR_PATTERN.search(normalized):
        return False
    segment = _first_command_segment(normalized)
    if not segment:
        return False
    if segment != normalized:
        return False
    for raw_prefix in allowlist:
        prefix = str(raw_prefix).strip()
        if not prefix:
            continue
        if _matches_prefix_with_boundary(segment, prefix):
            return True
    return False


def _command_allowed(
    *,
    command: str,
    full_access_enabled: bool,
    config: ExcelManusConfig,
) -> bool:
    if full_access_enabled:
        return True
    allowlist = tuple(config.hooks_command_allowlist)
    if allowlist and _allowlist_matches_command(command=command, allowlist=allowlist):
        return True
    return False


def run_command_handler(
    *,
    command: str,
    payload: dict[str, Any],
    full_access_enabled: bool,
    config: ExcelManusConfig,
) -> HookResult:
    command = (command or "").strip()
    if not command:
        return HookResult(
            decision=HookDecision.CONTINUE,
            reason="command hook 未配置命令，已跳过",
        )
    if not config.hooks_command_enabled:
        return HookResult(
            decision=HookDecision.CONTINUE,
            reason="command hook 已禁用（hooks_command_enabled=false）",
        )
    if not _command_allowed(
        command=command,
        full_access_enabled=full_access_enabled,
        config=config,
    ):
        return HookResult(
            decision=HookDecision.CONTINUE,
            reason="command hook 未获授权（需 allowlist 或 fullAccess）",
        )

    try:
        args = shlex.split(command)
    except ValueError as exc:
        return HookResult(
            decision=HookDecision.CONTINUE,
            reason=f"command hook 命令解析失败：{exc}",
        )
    if not args:
        return HookResult(
            decision=HookDecision.CONTINUE,
            reason="command hook 未配置命令，已跳过",
        )

    try:
        completed = subprocess.run(
            args,
            shell=False,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=config.hooks_command_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return HookResult(
            decision=HookDecision.CONTINUE,
            reason="command hook 超时，已跳过",
        )
    except Exception as exc:  # noqa: BLE001
        return HookResult(
            decision=HookDecision.CONTINUE,
            reason=f"command hook 执行失败：{exc}",
        )

    stdout = (completed.stdout or "").strip()
    if not stdout:
        return HookResult(
            decision=HookDecision.CONTINUE,
            reason="command hook 未返回内容，已跳过",
        )

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return HookResult(
            decision=HookDecision.CONTINUE,
            reason="command hook 输出非 JSON，已跳过",
            additional_context=stdout[: config.hooks_output_max_chars],
        )

    if not isinstance(parsed, dict):
        return HookResult(
            decision=HookDecision.CONTINUE,
            reason="command hook 输出不是对象，已跳过",
        )
    result = _build_result_from_mapping(parsed)
    if result.additional_context:
        result.additional_context = result.additional_context[: config.hooks_output_max_chars]
    return result


def run_prompt_handler(*, config_map: dict[str, Any]) -> HookResult:
    return _build_result_from_mapping(config_map)


def _parse_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def _build_agent_action(config_map: dict[str, Any]) -> HookAgentAction | None:
    merged = dict(config_map)
    hook_specific = _to_dict(config_map.get("hookSpecificOutput"))
    agent_action = _to_dict(hook_specific.get("agentAction"))
    if agent_action:
        merged.update(agent_action)
    elif hook_specific:
        merged.update(hook_specific)

    task = _parse_optional_str(
        merged.get("task")
        or merged.get("agent_task")
        or merged.get("prompt")
    )
    if task is None:
        return None
    agent_name = _parse_optional_str(
        merged.get("agent_name") or merged.get("agent-name")
    )
    on_failure = (
        _parse_optional_str(merged.get("on_failure") or merged.get("on-failure"))
        or "continue"
    ).lower()
    if on_failure not in {"continue", "deny"}:
        on_failure = "continue"
    inject_summary_as_context = _parse_bool(
        merged.get("inject_summary_as_context")
        if "inject_summary_as_context" in merged
        else merged.get("inject-summary-as-context"),
        default=True,
    )
    return HookAgentAction(
        task=task,
        agent_name=agent_name,
        on_failure=on_failure,  # type: ignore[arg-type]
        inject_summary_as_context=inject_summary_as_context,
    )


def run_agent_handler(*, config_map: dict[str, Any]) -> HookResult:
    result = _build_result_from_mapping(config_map)
    result.agent_action = _build_agent_action(config_map)
    return result
