"""Shared dispatch contract. Delivery IDs are independent of model requests."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

DispatchMode = Literal["steer", "interrupt", "queue"]
DISPATCH_MODES = frozenset({"steer", "interrupt", "queue"})
PENDING_DISPATCH = frozenset({"accepted", "queued", "interrupt_pending"})
TERMINAL_DISPATCH = frozenset({"completed", "failed", "cancelled", "interrupted"})


class DispatchConflict(ValueError):
    pass


def fingerprint(content: str, mode: str, extra: dict[str, Any]) -> str:
    payload = {key: extra.get(key) for key in (
        "images", "context_input", "chat_mode", "prompt_kind",
    )}
    context = payload.get("context_input") or {}
    payload["context_input"] = {key: context.get(key) for key in ("sheet_context", "sheet_contexts", "workbook_action")}
    payload["images"] = payload.get("images") or []
    payload.update(content=content, mode=mode)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def public_receipt(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in {"fingerprint", "reply"}}
