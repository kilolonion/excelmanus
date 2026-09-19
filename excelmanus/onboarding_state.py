"""新手引导进度：写入全局 config_kv，跨浏览器共享。

权威在服务端。浏览器 localStorage 只作一次性迁移源，不再作为完成标记。
已配置模型但还没有记录时，只推断 wizard 已完成，避免再走一遍密钥配置；
界面导览仍等某次真实完成后才算结束。
"""
from __future__ import annotations

import json
from typing import Any

ONBOARDING_KEY = "onboarding"
VALID_PHASES = frozenset(
    {"basic", "transition", "advanced", "settingsTransition", "settings", "done"}
)


def default_onboarding_state() -> dict[str, Any]:
    return {
        "wizard_completed": False,
        "coach_marks_completed": False,
        "advanced_guide_completed": False,
        "settings_guide_completed": False,
        "skipped_at": None,
        "coach_phase": "basic",
        "coach_step_index": 0,
    }


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return default


def normalize_onboarding_state(raw: Any) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    state = default_onboarding_state()
    state["wizard_completed"] = _as_bool(src.get("wizard_completed"))
    state["coach_marks_completed"] = _as_bool(src.get("coach_marks_completed"))
    state["advanced_guide_completed"] = _as_bool(src.get("advanced_guide_completed"))
    state["settings_guide_completed"] = _as_bool(src.get("settings_guide_completed"))
    skipped = src.get("skipped_at")
    state["skipped_at"] = skipped if isinstance(skipped, str) and skipped.strip() else None
    phase = src.get("coach_phase")
    if isinstance(phase, str) and phase in VALID_PHASES:
        state["coach_phase"] = phase
    try:
        index = int(src.get("coach_step_index", 0) or 0)
    except (TypeError, ValueError):
        index = 0
    state["coach_step_index"] = max(0, index)
    return state


def load_onboarding_state(store: Any, *, configured: bool) -> dict[str, Any]:
    """读取已保存进度；没有记录且后端已配置时，跳过配置向导。"""
    if store is not None:
        raw = store.get(ONBOARDING_KEY, "")
        if raw:
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                return normalize_onboarding_state(parsed)
    state = default_onboarding_state()
    if configured:
        state["wizard_completed"] = True
    return state


def save_onboarding_state(store: Any, payload: Any) -> dict[str, Any]:
    if store is None:
        raise RuntimeError("config store unavailable")
    state = normalize_onboarding_state(payload)
    store.set(ONBOARDING_KEY, json.dumps(state, ensure_ascii=False))
    return state
