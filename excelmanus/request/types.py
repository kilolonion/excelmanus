"""Batch 2 request value types. No I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping
from types import MappingProxyType


def freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    return value


def thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value

SeriesEventKind = Literal[
    "series/start",
    "turn/append",
    "policy/update",
    "surface/compact",
    "catalog/change",
    "route/change",
    "transport/renew",
    "cache/policy",
    "cache/config",
    "restore/migrate",
    "rollback/edit",
    "vision/change",
    "attachment_quota",
    "request/degrade",
    "request/accepted",
    "request/failed",
    "request/cancelled",
]

REWRITE_EVENTS = frozenset({
    "series/start",
    "policy/update",
    "surface/compact",
    "catalog/change",
    "route/change",
    "restore/migrate",
    "rollback/edit",
    "vision/change",
    "attachment_quota",
})


@dataclass(frozen=True)
class ResolvedRoute:
    session_id: str
    model: str
    protocol: str
    endpoint: str
    credential_scope: str
    api_key: str
    thinking: Mapping[str, Any] = field(default_factory=dict)
    capabilities: Mapping[str, bool] = field(default_factory=dict)
    files_purpose: str | None = None
    call_config: Mapping[str, Any] = field(default_factory=dict)
    workspace_key: str | None = None

    def __post_init__(self) -> None:
        for name in ("thinking", "capabilities", "call_config"):
            object.__setattr__(self, name, freeze_json(getattr(self, name)))

    def protocol_label(self) -> str:
        if self.endpoint:
            return f"{self.protocol}|{self.endpoint}"
        return self.protocol or "unknown"

    def route_fingerprint_material(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "protocol": self.protocol,
            "endpoint": self.endpoint,
            "credential_scope": self.credential_scope,
            "call_config": thaw_json(self.call_config),
        }


@dataclass(frozen=True)
class MessageSource:
    kind: Literal["user", "assistant", "tool", "context", "system_head", "system_update"]
    provider: str | None = None
    model: str | None = None
    replay_state: Any = None
    thinking_text: str | None = None
    tool_call_id: str | None = None


@dataclass
class SourcedMessage:
    message_id: str
    source: MessageSource
    role: str
    content: Any
    tool_calls: list | None = None


@dataclass(frozen=True)
class RequestHeader:
    route_fingerprint: str
    tools_digest: str
    catalog_digest: str
    system_head_digest: str
    content_identity: str
    content_payload: str
    cache_policy_digest: str
    transport: Literal["inline", "file"]
    prompt_cache_key: str = ""
    provider_digest: str = ""
    # Native request evidence. Full digest is diagnostic; message/block hashes
    # may only grow, while settings changes are recorded separately.
    provider_config_digest: str = ""
    provider_prefix: tuple[str, ...] = ()
    continuation_id: str = ""
    file_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_fingerprint": self.route_fingerprint,
            "tools_digest": self.tools_digest,
            "catalog_digest": self.catalog_digest,
            "system_head_digest": self.system_head_digest,
            "content_identity": self.content_identity,
            "content_payload": self.content_payload,
            "cache_policy_digest": self.cache_policy_digest,
            "transport": self.transport,
            "prompt_cache_key": self.prompt_cache_key,
            "provider_digest": self.provider_digest,
            "provider_config_digest": self.provider_config_digest,
            "provider_prefix": list(self.provider_prefix),
            "continuation_id": self.continuation_id,
            "file_ids": list(self.file_ids),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> RequestHeader | None:
        if not isinstance(raw, dict):
            return None
        payload = raw.get("content_payload")
        identity = raw.get("content_identity")
        if not isinstance(payload, str) or not payload:
            return None
        if not isinstance(identity, str) or not identity:
            return None
        for key in ("provider_prefix", "file_ids"):
            value = raw.get(key, [])
            if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
                return None
        transport = raw.get("transport") or "inline"
        if transport not in {"inline", "file"}:
            transport = "inline"
        return cls(
            route_fingerprint=str(raw.get("route_fingerprint") or ""),
            tools_digest=str(raw.get("tools_digest") or ""),
            catalog_digest=str(raw.get("catalog_digest") or ""),
            system_head_digest=str(raw.get("system_head_digest") or ""),
            content_identity=identity,
            content_payload=payload,
            cache_policy_digest=str(raw.get("cache_policy_digest") or ""),
            transport=transport,  # type: ignore[arg-type]
            prompt_cache_key=str(raw.get("prompt_cache_key") or ""),
            provider_digest=str(raw.get("provider_digest") or ""),
            provider_config_digest=str(raw.get("provider_config_digest") or ""),
            provider_prefix=tuple(str(item) for item in (raw.get("provider_prefix") or [])),
            continuation_id=str(raw.get("continuation_id") or ""),
            file_ids=tuple(str(item) for item in (raw.get("file_ids") or [])),
        )


@dataclass(frozen=True)
class PreparedRequest:
    request_id: str
    series_id: str
    attempt: int
    header: RequestHeader
    route: ResolvedRoute
    provider_body: Mapping[str, Any]
    file_leases: tuple[str, ...]
    compiled_at: float
    transport_headers: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_body", freeze_json(self.provider_body))
        object.__setattr__(self, "transport_headers", freeze_json(self.transport_headers))

    def create_kwargs(self) -> dict[str, Any]:
        body = thaw_json(self.provider_body)
        if self.route.protocol in {
            "anthropic", "gemini", "openai_responses", "antigravity",
        }:
            # The adapter's transport consumes this exact compiled native body.
            kwargs = {"model": self.route.model, "messages": [], "_prepared_body": body}
        else:
            kwargs = body
        if self.transport_headers:
            kwargs["extra_headers"] = thaw_json(self.transport_headers)
        return kwargs


@dataclass(frozen=True)
class CacheUsage:
    """hit/write 为 None 表示未知，禁止把未知写成 0。"""

    hit: int | None
    write: int | None = None
    prompt_tokens: int = 0
    miss_reason: str = "unknown"
