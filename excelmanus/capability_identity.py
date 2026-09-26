"""Secret-free namespaces for capability observations (not model-name guesses)."""
import hashlib
import json

PROBE_VERSION = 2


def json_object(raw) -> dict:
    """Legacy malformed optional settings must not break read-only listings."""
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def capability_scope(protocol: str = "auto", api_key: str = "", headers=None, *, base_url: str = "", model: str = "", thinking_mode: str = "auto", extra_body: str = "") -> str:
    from excelmanus.prompt.envelope import normalize_protocol
    resolved = normalize_protocol(protocol=protocol, base_url=base_url, model=model).split("|", 1)[0]
    material = [PROBE_VERSION, resolved, api_key, headers or {}, thinking_mode, extra_body]
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def scope_from_client(client, model: str, base_url: str, thinking_mode: str = "auto", extra_body: str | None = None, extra_headers: dict | None = None) -> str:
    identity = getattr(client, "_capability_identity", None)
    if not isinstance(identity, dict):
        identity = {"protocol": "auto", "api_key": str(getattr(client, "api_key", ""))}
    identity = {**identity, "headers": {**(identity.get("headers") or {}), **(extra_headers or {})}}
    return capability_scope(**identity, model=model, base_url=base_url, thinking_mode=thinking_mode,
                            extra_body=(extra_body if extra_body is not None else getattr(client, "_capability_extra_body", "")) or "")


def active_observation(engine):
    """In-memory observations must obey the same identity and TTL as SQLite."""
    caps = getattr(engine, "_model_capabilities", None)
    if caps is None:
        return None
    scope = getattr(caps, "cache_scope", "")
    if scope:
        if scope != getattr(engine, "capability_scope", None):
            return None
        from excelmanus.model_probe import capabilities_cache_is_fresh
        if not capabilities_cache_is_fresh(caps):
            # Partial/unknown dimensions remain useful during their cache TTL.
            from datetime import datetime, timezone
            try:
                expires = datetime.fromisoformat(caps.fresh_until)
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) >= expires:
                    return None
            except (ValueError, TypeError, AttributeError):
                return None
    return caps
