"""In-process provider breaker for Jev auxiliary calls.

The breaker is intentionally small and process-local. Persistent provider
configuration remains in the main store; transient transport health should
not be persisted as user configuration.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock


FAILURE_COOLDOWN_SECONDS = 20.0
FAILURE_THRESHOLD = 2


@dataclass
class _State:
    failures: int = 0
    opened_at: float = 0.0
    last_reason: str = ""


_LOCK = Lock()
_STATES: dict[str, _State] = {}


def provider_key(provider_id: str, protocol: str, model: str, base_url: str) -> str:
    return "|".join((provider_id or "unknown", protocol or "", model or "", base_url or ""))


def allow(key: str) -> bool:
    with _LOCK:
        state = _STATES.get(key)
        if state is None or not state.opened_at:
            return True
        if time.monotonic() - state.opened_at >= FAILURE_COOLDOWN_SECONDS:
            state.opened_at = 0.0
            state.failures = 0
            return True
        return False


def record_failure(key: str, reason: str) -> None:
    with _LOCK:
        state = _STATES.setdefault(key, _State())
        state.failures += 1
        state.last_reason = str(reason or "error")[:120]
        if state.failures >= FAILURE_THRESHOLD:
            state.opened_at = time.monotonic()


def record_success(key: str) -> None:
    with _LOCK:
        _STATES.pop(key, None)


def snapshot() -> dict[str, dict[str, object]]:
    with _LOCK:
        now = time.monotonic()
        return {
            key: {
                "failures": state.failures,
                "open": bool(state.opened_at and now - state.opened_at < FAILURE_COOLDOWN_SECONDS),
                "last_reason": state.last_reason,
            }
            for key, state in _STATES.items()
        }


def reset() -> None:
    with _LOCK:
        _STATES.clear()
