"""窗口感知小模型提示词与解析器。"""

from __future__ import annotations

import json
import re
from typing import Any

from .advisor import LifecyclePlan, WindowAdvice
from .advisor_context import AdvisorContext
from .models import PerceptionBudget, WindowState

TASK_TYPES: tuple[str, ...] = (
    "DATA_COMPARISON",
    "FORMAT_CHECK",
    "FORMULA_DEBUG",
    "DATA_ENTRY",
    "ANOMALY_SEARCH",
    "GENERAL_BROWSE",
)

_VALID_TIERS: set[str] = {"active", "background", "suspended", "terminated"}
_TASK_TYPE_SET = set(TASK_TYPES)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def build_advisor_messages(
    *,
    windows: list[WindowState],
    active_window_id: str | None,
    budget: PerceptionBudget,
    context: AdvisorContext,
) -> list[dict[str, str]]:
    """构建窗口生命周期小模型请求消息。"""
    compact_windows: list[dict[str, Any]] = []
    for item in windows[:8]:
        viewport = item.viewport
        compact_windows.append(
            {
                "id": item.id,
                "type": item.type.value,
                "file_path": item.file_path or "",
                "sheet_name": item.sheet_name or "",
                "idle_turns": item.idle_turns,
                "last_access_seq": item.last_access_seq,
                "summary": (item.summary or "")[:120],
                "viewport": {
                    "range": viewport.range_ref if viewport else "",
                    "rows": viewport.total_rows if viewport else 0,
                    "cols": viewport.total_cols if viewport else 0,
                },
            }
        )

    payload = {
        "active_window_id": active_window_id,
        "turn_context": {
            "turn_number": context.turn_number,
            "is_new_task": context.is_new_task,
            "window_count_changed": context.window_count_changed,
            "user_intent_summary": context.user_intent_summary[:200],
            "agent_recent_output": context.agent_recent_output[:200],
            "task_type_hint": context.task_type or "GENERAL_BROWSE",
        },
        "budget": {
            "system_budget_tokens": budget.system_budget_tokens,
            "max_windows": budget.max_windows,
            "minimized_tokens": budget.minimized_tokens,
            "background_after_idle": budget.background_after_idle,
            "suspend_after_idle": budget.suspend_after_idle,
            "terminate_after_idle": budget.terminate_after_idle,
        },
        "windows": compact_windows,
    }

    system_prompt = (
        "你是窗口生命周期顾问。只输出 JSON 对象，不要输出解释。"
        "你必须返回字段 task_type 和 advices。"
        "task_type 只能是 "
        + ", ".join(TASK_TYPES)
        + "。"
        "advices 是数组，每项包含 window_id、tier、reason、custom_summary。"
        "tier 只能是 active/background/suspended/terminated。"
    )
    user_prompt = (
        "请根据输入窗口状态给出下一轮窗口生命周期建议。\n"
        "输出 JSON 结构示例：\n"
        '{"task_type":"GENERAL_BROWSE","advices":[{"window_id":"sheet_1","tier":"background","reason":"idle=2","custom_summary":"已完成"}]}\n'
        "输入如下：\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_small_model_plan(text: str) -> LifecyclePlan | None:
    """解析小模型输出为生命周期计划。"""
    parsed = _parse_json_object(text)
    if not isinstance(parsed, dict):
        return None

    task_type = str(parsed.get("task_type", "")).strip().upper()
    if task_type not in _TASK_TYPE_SET:
        return None

    raw_advices = parsed.get("advices")
    if not isinstance(raw_advices, list):
        return None

    advices: list[WindowAdvice] = []
    for raw in raw_advices:
        if not isinstance(raw, dict):
            continue
        window_id = str(raw.get("window_id", "")).strip()
        tier = str(raw.get("tier", "")).strip().lower()
        if not window_id or tier not in _VALID_TIERS:
            continue
        reason = str(raw.get("reason", "")).strip()[:120]
        custom_summary: str | None = None
        if isinstance(raw.get("custom_summary"), str):
            summary = str(raw.get("custom_summary")).strip()
            custom_summary = summary[:120] if summary else None
        advices.append(
            WindowAdvice(
                window_id=window_id,
                tier=tier,  # type: ignore[arg-type]
                reason=reason,
                custom_summary=custom_summary,
            )
        )

    generated_turn = _to_int(parsed.get("generated_turn"))
    return LifecyclePlan(
        advices=advices,
        source="small_model",
        task_type=task_type,
        generated_turn=generated_turn,
    )


def _parse_json_object(text: str) -> dict[str, Any] | None:
    for candidate in _iter_json_candidates(text):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _iter_json_candidates(text: str) -> list[str]:
    content = (text or "").strip()
    if not content:
        return []

    candidates = [content]
    for match in _JSON_FENCE_RE.finditer(content):
        body = (match.group(1) or "").strip()
        if body:
            candidates.append(body)

    left = content.find("{")
    right = content.rfind("}")
    if left >= 0 and right > left:
        candidates.append(content[left : right + 1].strip())
    return candidates


def _to_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0
