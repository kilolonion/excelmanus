"""ChatResult → SubagentResult。从工具批回填变更与已观察文件。"""

from __future__ import annotations

from typing import Any

from excelmanus.subagent.models import (
    SubagentConfig,
    SubagentFileChange,
    SubagentResult,
    SubagentStopReason,
)
from excelmanus.tools.policy import (
    AUDIT_TARGET_ARG_RULES_ALL,
    MUTATING_ALL_TOOLS,
    READ_ONLY_SAFE_TOOLS,
)

_DIAGNOSTIC_LIMIT = 4096

_CHANGE_TYPE = {
    "edit_spreadsheet": "write",
    "format_spreadsheet": "format",
    "delete_file": "delete",
    "write_text_file": "write",
    "edit_text_file": "write",
    "copy_file": "create",
    "rename_file": "write",
    "write_word": "write",
    "manage_spreadsheet_objects": "write",
    "manage_spreadsheet_versions": "write",
    "split_spreadsheet": "create",
    "run_shell": "write",
}

_PATH_KEYS = (
    "file_path",
    "path",
    "destination",
    "source",
    "target",
)


def bound_diagnostic(text: str) -> str:
    """有界诊断，不含工具入参。按 UTF-8 字节截断。"""
    raw = (text or "").encode("utf-8")
    if len(raw) <= _DIAGNOSTIC_LIMIT:
        return text or ""
    return raw[:_DIAGNOSTIC_LIMIT].decode("utf-8", errors="ignore")


def _paths_from_args(arguments: dict[str, Any] | None, tool_name: str) -> list[str]:
    if not isinstance(arguments, dict):
        return []
    keys = AUDIT_TARGET_ARG_RULES_ALL.get(tool_name, _PATH_KEYS)
    paths: list[str] = []
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())
    if paths:
        return paths
    for key in _PATH_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())
    return paths


def collect_structured_changes(tool_calls: list[Any]) -> list[SubagentFileChange]:
    changes: list[SubagentFileChange] = []
    for tc in tool_calls:
        name = str(getattr(tc, "tool_name", "") or "")
        if name not in MUTATING_ALL_TOOLS:
            continue
        if getattr(tc, "success", False) is False:
            continue
        args = getattr(tc, "arguments", None)
        sheets = args.get("sheet_name") if isinstance(args, dict) else None
        sheet_tuple: tuple[str, ...] = ()
        if isinstance(sheets, str) and sheets.strip():
            sheet_tuple = (sheets.strip(),)
        elif isinstance(sheets, list):
            sheet_tuple = tuple(str(s) for s in sheets if str(s).strip())
        for path in _paths_from_args(args if isinstance(args, dict) else None, name):
            changes.append(
                SubagentFileChange(
                    path=path,
                    tool_name=name,
                    change_type=_CHANGE_TYPE.get(name, "write"),
                    sheets_affected=sheet_tuple,
                )
            )
    return changes


def collect_observed_files(tool_calls: list[Any]) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for tc in tool_calls:
        name = str(getattr(tc, "tool_name", "") or "")
        if name not in READ_ONLY_SAFE_TOOLS and name not in MUTATING_ALL_TOOLS:
            continue
        args = getattr(tc, "arguments", None)
        for path in _paths_from_args(args if isinstance(args, dict) else None, name):
            if path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def _is_denied(tc: Any) -> bool:
    error = str(getattr(tc, "error", "") or "")
    result = str(getattr(tc, "result", "") or "")
    return (
        error in {"PRE_EXECUTE_DENIED", "PERMISSION_DENIED"}
        or "拒绝写入" in result
        or "写入被拒绝" in result
        or "PRE_EXECUTE_DENIED" in result
    )


def chat_to_result(
    chat: Any,
    *,
    config: SubagentConfig,
    conversation_id: str,
    stop_reason: SubagentStopReason | None = None,
    diagnostic: str | None = None,
) -> SubagentResult:
    """把子循环 ChatResult 收成终态。失败不抛。"""
    tool_calls = list(getattr(chat, "tool_calls", None) or []) if chat is not None else []
    output = str(getattr(chat, "reply", "") or "") if chat is not None else ""
    truncated = bool(getattr(chat, "truncated", False)) if chat is not None else False
    denied = [_is_denied(tc) for tc in tool_calls]
    reason: SubagentStopReason
    diag = diagnostic
    if stop_reason is not None:
        reason = stop_reason
    elif chat is None:
        reason = "error"
        diag = diag or "子 Driver 没有返回结果。"
        output = output or diag
    elif any(denied):
        reason = "refusal"
        last = next(tc for tc, flag in zip(tool_calls, denied) if flag)
        diag = bound_diagnostic(
            str(getattr(last, "result", None) or getattr(last, "error", "") or "PRE_EXECUTE_DENIED")
        )
        output = output or diag
    elif truncated:
        reason = "max-tokens"
        diag = bound_diagnostic(output or "子代理在 token 上限处停止。")
    else:
        reason = "completed"

    return SubagentResult(
        stop_reason=reason,
        output=output,
        diagnostic=bound_diagnostic(diag) if diag else None,
        subagent_name=config.name,
        permission_mode=config.permission_mode,
        conversation_id=conversation_id,
        iterations=int(getattr(chat, "iterations", 0) or 0) if chat is not None else 0,
        tool_calls_count=len(tool_calls),
        prompt_tokens=int(getattr(chat, "prompt_tokens", 0) or 0) if chat is not None else 0,
        completion_tokens=int(getattr(chat, "completion_tokens", 0) or 0) if chat is not None else 0,
        structured_changes=collect_structured_changes(tool_calls),
        observed_files=collect_observed_files(tool_calls),
    )


def format_parent_reply(result: SubagentResult) -> str:
    """主模型看到的工具结果文本。"""
    if result.stop_reason == "completed":
        return result.output
    hint = ""
    if (result.observed_files or result.structured_changes) and "已完成的工作" not in result.output:
        hint = (
            "（已保留部分产出"
            f"：发现文件 {len(result.observed_files)} 个"
            f"，结构化变更 {len(result.structured_changes)} 条）"
        )
    detail = result.diagnostic or result.output or result.stop_reason
    return f"子代理执行失败（{result.subagent_name}）：{detail}{hint}"


def failure_result(
    *,
    config: SubagentConfig,
    conversation_id: str,
    stop_reason: SubagentStopReason,
    message: str,
) -> SubagentResult:
    text = bound_diagnostic(message)
    return SubagentResult(
        stop_reason=stop_reason,
        output=text,
        diagnostic=text,
        subagent_name=config.name,
        permission_mode=config.permission_mode,
        conversation_id=conversation_id,
    )
