"""Single resolved route per attempt. All consumers read this snapshot."""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse
from typing import Any

from excelmanus.prompt.envelope import (
    _canonical_endpoint,
    call_config_from_engine,
    normalize_protocol,
)
from excelmanus.request.types import ResolvedRoute


def credential_scope(endpoint: str, api_key: str | None) -> str:
    """Irreversible namespace from full credential + canonical endpoint."""
    material = f"{_canonical_endpoint(endpoint)}\0{api_key or ''}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _protocol_name(label: str) -> str:
    text = (label or "").strip().lower()
    if "|" in text:
        text = text.split("|", 1)[0]
    return text or "openai"


def resolve_route(engine: Any) -> ResolvedRoute:
    """Build the only route snapshot for this attempt from active client state."""
    config = getattr(engine, "_config", None) or getattr(engine, "config", None)
    profile = getattr(engine, "_active_profile", None)
    proto = str(getattr(engine, "_active_protocol", "") or "")
    if not proto and profile is not None:
        proto = str(getattr(profile, "protocol", "") or "")
    endpoint = str(getattr(engine, "_active_base_url", "") or "")
    if not endpoint and config is not None:
        endpoint = str(getattr(config, "base_url", "") or "")
    model = str(
        getattr(engine, "_active_model", None)
        or getattr(config, "model", None)
        or ""
    )
    api_key = str(getattr(engine, "_active_api_key", "") or "")
    if not api_key and config is not None:
        api_key = str(getattr(config, "api_key", "") or "")
    label = normalize_protocol(protocol=proto, base_url=endpoint, model=model)
    protocol = _protocol_name(label)
    host = (urlparse(endpoint).hostname or "").lower()
    stateless_responses = host == "api.deepseek.com" or host.endswith(".deepseek.com")
    files_ok = protocol in {"openai", "openai_responses"}
    purpose = None
    if files_ok:
        purpose = "user_data" if "deepseek" in (endpoint or "").lower() else "assistants"
    thinking: dict[str, Any] = {}
    tc = getattr(engine, "_thinking_config", None)
    if tc is not None:
        thinking = {
            "effort": getattr(tc, "effort", None),
            "budget_tokens": getattr(tc, "budget_tokens", 0),
        }
    session_id = str(getattr(engine, "_session_id", "") or "")
    workspace_key = None
    binding = getattr(engine, "_session_binding", None)
    workspace = getattr(binding, "workspace", None) if binding is not None else None
    if workspace is not None:
        key_fn = getattr(workspace, "identity_key", None)
        if callable(key_fn):
            workspace_key = str(key_fn())
    return ResolvedRoute(
        session_id=session_id,
        model=model,
        protocol=protocol,
        endpoint=endpoint,
        credential_scope=credential_scope(endpoint, api_key),
        api_key=api_key,
        thinking=thinking,
        capabilities={
            "vision": bool(getattr(engine, "_is_vision_capable", True)),
            "files": files_ok,
            "prompt_cache_key": bool(getattr(config, "prompt_cache_key_enabled", True))
            if config is not None
            else True,
            "mid_history_system": protocol in {"openai"},
            "stored_responses": protocol == "openai_responses" and not stateless_responses,
        },
        files_purpose=purpose,
        call_config=call_config_from_engine(engine),
        workspace_key=workspace_key,
    )


def protocol_from_engine(engine: Any) -> str:
    """Active-route protocol label. Replaces config.base_url dual-read."""
    return resolve_route(engine).protocol_label()
