"""重启 / 跨 worker 重建后的信封前缀等值校验。

只告警、不 fail-closed，也不改写信封前缀不变量。
把上次请求的 tools/system 摘要随会话快照持久化，重建后首个请求若摘要不同，
日志明确指出将静默打满 prompt cache miss。
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("excelmanus.prompt.cache_restore")

PREFIX_STATE_KEY = "envelope_prefix"
_COMPARE_KEYS = ("tools_digest", "system_digest", "catalog_digest", "skill_names")
_EPOCH_SNAPSHOT_KEYS = (
    "epoch_key",
    "model",
    "protocol",
    "call_config_digest",
    "wire_digest",
)


def active_skill_names(engine: Any) -> list[str]:
    skills = getattr(engine, "_active_skills", None) or []
    names: list[str] = []
    for skill in skills:
        name = getattr(skill, "name", None)
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def capture_prefix_snapshot(envelope: Any, engine: Any) -> dict[str, Any]:
    identity = getattr(envelope, "identity", None)
    snapshot: dict[str, Any] = {
        "tools_digest": str(getattr(identity, "tools_digest", "") or ""),
        "system_digest": str(getattr(identity, "system_digest", "") or ""),
        "catalog_digest": str(getattr(identity, "catalog_digest", "") or ""),
        "skill_names": active_skill_names(engine),
    }
    epoch = getattr(envelope, "epoch", None)
    if epoch is not None:
        key_fn = getattr(epoch, "key", None)
        snapshot["epoch_key"] = str(getattr(envelope, "prompt_cache_key", key_fn() if callable(key_fn) else "") or "")
        snapshot["model"] = str(getattr(epoch, "model", "") or "")
        snapshot["protocol"] = str(getattr(epoch, "protocol", "") or "")
        snapshot["call_config_digest"] = str(getattr(epoch, "call_config_digest", "") or "")
        snapshot["wire_digest"] = str(getattr(epoch, "wire_digest", "") or "")
    return snapshot


def normalize_snapshot(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    tools = raw.get("tools_digest")
    system = raw.get("system_digest")
    if not isinstance(tools, str) or not tools:
        return None
    if not isinstance(system, str) or not system:
        return None
    catalog = raw.get("catalog_digest")
    if not isinstance(catalog, str):
        catalog = ""
    skills = raw.get("skill_names")
    if isinstance(skills, str):
        names = [skills] if skills.strip() else []
    elif isinstance(skills, (list, tuple)):
        names = [str(item).strip() for item in skills if str(item).strip()]
    else:
        names = []
    return {
        "tools_digest": tools,
        "system_digest": system,
        "catalog_digest": catalog,
        "skill_names": names,
        **{
            key: str(raw[key])
            for key in _EPOCH_SNAPSHOT_KEYS
            if isinstance(raw.get(key), str) and str(raw.get(key))
        },
    }


def prefix_drift_fields(persisted: Any, current: Any) -> list[str]:
    left = normalize_snapshot(persisted)
    right = normalize_snapshot(current)
    if left is None or right is None:
        return []
    drifted: list[str] = []
    for key in _COMPARE_KEYS:
        if left[key] != right[key]:
            drifted.append(key)
    return drifted


def attach_prefix_to_state_dict(
    state_dict: dict[str, Any],
    snapshot: Any,
) -> dict[str, Any]:
    normalized = normalize_snapshot(snapshot)
    if normalized is None:
        return state_dict
    state_dict[PREFIX_STATE_KEY] = normalized
    return state_dict


def extract_restored_prefix(state_dict: Any) -> dict[str, Any] | None:
    if not isinstance(state_dict, dict):
        return None
    return normalize_snapshot(state_dict.get(PREFIX_STATE_KEY))


def remember_prefix_snapshot(engine: Any, envelope: Any) -> None:
    engine._envelope_prefix_snapshot = capture_prefix_snapshot(envelope, engine)


def warn_if_restored_prefix_drifted(engine: Any, envelope: Any) -> bool:
    persisted = getattr(engine, "_restored_envelope_prefix", None)
    engine._restored_envelope_prefix = None
    current = capture_prefix_snapshot(envelope, engine)
    drifted = prefix_drift_fields(persisted, current)
    if not drifted:
        return False
    session_id = getattr(engine, "_session_id", None) or "?"
    logger.warning(
        "会话 %s 重建后信封前缀与上次请求不等值（%s）。"
        "将静默打满 prompt cache miss。"
        "常见原因：跨 worker / 进程重启后 MCP 未就绪、技能快照丢失。"
        " persisted=%s current=%s",
        session_id,
        ", ".join(drifted),
        persisted,
        current,
    )
    return True


def configured_web_workers() -> int:
    for key in ("EXCELMANUS_WEB_WORKERS", "WEB_CONCURRENCY"):
        raw = os.environ.get(key)
        if raw is None or not str(raw).strip():
            continue
        try:
            return max(1, int(str(raw).strip()))
        except ValueError:
            continue
    return 1


def multi_worker_cache_warning(workers: int | None = None) -> str | None:
    count = configured_web_workers() if workers is None else int(workers)
    if count <= 1:
        return None
    return (
        f"检测到 {count} 个 uvicorn worker。会话引擎是进程内存态，同一 session_id "
        "落到不同 worker 会从 SQLite 重建信封；MCP 未连上或技能快照丢失时 "
        "tools/system 前缀不等值，将静默打满 prompt cache miss。"
        "单机请保持 workers=1；多实例扩容请在反代层按 session_id 粘性路由。"
    )


def log_multi_worker_cache_risk(workers: int | None = None) -> None:
    text = multi_worker_cache_warning(workers)
    if text:
        logger.warning("%s", text)
