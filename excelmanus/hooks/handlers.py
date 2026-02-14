"""Hook 处理器。"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from excelmanus.config import ExcelManusConfig
from excelmanus.hooks.models import HookDecision, HookResult


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


def _command_allowed(
    *,
    command: str,
    full_access_enabled: bool,
    config: ExcelManusConfig,
) -> bool:
    if full_access_enabled:
        return True
    allowlist = tuple(config.hooks_command_allowlist)
    if allowlist and any(command.startswith(prefix) for prefix in allowlist):
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
        completed = subprocess.run(
            command,
            shell=True,
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


def run_agent_handler(*, config_map: dict[str, Any]) -> HookResult:
    # 当前阶段先支持静态决策语义，后续可接入真实子代理执行。
    return _build_result_from_mapping(config_map)
