"""In-process registry for deferred tool schema bundles.

Wire schemas carry a short ``schema_v1_*`` handle when their nested contract
is large.  ``introspect_capability(tool_detail, query=<handle>)`` resolves it
back to the full schema; the registry is rebuilt from ToolDef on process start
and never grants execution permission by itself.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from threading import RLock
from typing import Any

_LOCK = RLock()
_BUNDLES: dict[str, dict[str, Any]] = {}


def schema_id(tool_name: str, schema: dict[str, Any]) -> str:
    payload = json.dumps(
        {"tool": str(tool_name or ""), "schema": schema},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "schema_v1_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def register_schema(tool_name: str, schema: dict[str, Any]) -> str:
    handle = schema_id(tool_name, schema)
    with _LOCK:
        _BUNDLES[handle] = deepcopy(schema)
    return handle


def get_schema(handle: str) -> dict[str, Any] | None:
    with _LOCK:
        value = _BUNDLES.get(str(handle or ""))
        return deepcopy(value) if value is not None else None


def clear() -> None:
    with _LOCK:
        _BUNDLES.clear()

