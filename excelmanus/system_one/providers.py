"""Jev 决策提供商：TypeSafe / Vercel / 自定义。不进 model_profiles。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

GATEWAY_URL = "https://ai-gateway.vercel.sh/v4/ai/evaluation-model"
GATEWAY_MODEL = "typesafe-ai/jev"

JEV_PROVIDERS_SETTING = "EXCELMANUS_JEV_PROVIDERS"
JEV_ACTIVE_PROVIDER_SETTING = "EXCELMANUS_JEV_ACTIVE_PROVIDER"
TYPESAFE_KEY_SETTING = "EXCELMANUS_TYPESAFE_API_KEY"
GATEWAY_KEY_SETTING = "EXCELMANUS_AI_GATEWAY_API_KEY"

JEV_PROTOCOL_TYPESAFE = "typesafe"
JEV_PROTOCOL_GATEWAY = "gateway"

PROVIDER_ID_TYPESAFE = "typesafe"
PROVIDER_ID_VERCEL = "vercel"

TYPESAFE_BASE_URL = "https://api.typesafe.ai"
TYPESAFE_DEFAULT_MODEL = "jev-1.13.0"


@dataclass(frozen=True)
class JevProviderRecord:
    id: str
    name: str
    protocol: str
    base_url: str
    model: str
    api_key: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip())


@dataclass(frozen=True)
class JevConnection:
    """Resolved connection used by every Jev entry point.

    Keeping key, transport, endpoint and model together prevents the host,
    calibration CLI and trace layer from independently reimplementing the
    provider fallback rules.
    """

    provider_id: str = ""
    api_key: str | None = None
    protocol: str = ""
    base_url: str = ""
    model: str = ""
    source: str = ""


def jev_preset(provider_id: str) -> JevProviderRecord:
    if provider_id == PROVIDER_ID_VERCEL:
        return JevProviderRecord(
            id=PROVIDER_ID_VERCEL,
            name="Vercel",
            protocol=JEV_PROTOCOL_GATEWAY,
            base_url=GATEWAY_URL,
            model=GATEWAY_MODEL,
        )
    return JevProviderRecord(
        id=PROVIDER_ID_TYPESAFE,
        name="TypeSafe",
        protocol=JEV_PROTOCOL_TYPESAFE,
        base_url=TYPESAFE_BASE_URL,
        model=TYPESAFE_DEFAULT_MODEL,
    )


def normalize_jev_protocol(value: str | None) -> str:
    return JEV_PROTOCOL_GATEWAY if value == JEV_PROTOCOL_GATEWAY else JEV_PROTOCOL_TYPESAFE


def record_from_mapping(raw: Mapping[str, Any]) -> JevProviderRecord | None:
    provider_id = str(raw.get("id") or "").strip()
    if not provider_id:
        return None
    protocol = normalize_jev_protocol(str(raw.get("protocol") or ""))
    preset = jev_preset(provider_id) if provider_id in {PROVIDER_ID_TYPESAFE, PROVIDER_ID_VERCEL} else None
    name = str(raw.get("name") or "").strip() or (preset.name if preset else provider_id)
    base_url = str(raw.get("base_url") or "").strip() or (preset.base_url if preset else "")
    model = str(raw.get("model") or "").strip() or (preset.model if preset else TYPESAFE_DEFAULT_MODEL)
    return JevProviderRecord(
        id=provider_id,
        name=name,
        protocol=protocol if not preset else preset.protocol,
        base_url=base_url,
        model=model,
        api_key=str(raw.get("api_key") or "").strip(),
    )


def parse_jev_providers_json(raw: str | None) -> list[JevProviderRecord]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except ValueError:
        return []
    if not isinstance(payload, list):
        return []
    records: list[JevProviderRecord] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        record = record_from_mapping(item)
        if record is None or record.id in seen:
            continue
        seen.add(record.id)
        records.append(record)
    return records


def serialize_jev_providers(records: list[JevProviderRecord]) -> str:
    return json.dumps(
        [
            {
                "id": item.id,
                "name": item.name,
                "protocol": item.protocol,
                "base_url": item.base_url,
                "model": item.model,
                "api_key": item.api_key,
            }
            for item in records
        ],
        ensure_ascii=False,
    )


def public_jev_provider(record: JevProviderRecord) -> dict[str, object]:
    key = record.api_key.strip()
    return {
        "id": record.id,
        "name": record.name,
        "protocol": record.protocol,
        "base_url": record.base_url,
        "model": record.model,
        "configured": bool(key),
        "last4": key[-4:] if key else "",
    }


def migrate_legacy_jev_providers(
    typesafe_key: str | None,
    gateway_key: str | None,
) -> list[JevProviderRecord]:
    ts = (typesafe_key or "").strip()
    gw = (gateway_key or "").strip()
    records: list[JevProviderRecord] = []
    if ts and ts == gw:
        if ts.startswith("vck_"):
            records.append(replace(jev_preset(PROVIDER_ID_VERCEL), api_key=ts))
        else:
            records.append(replace(jev_preset(PROVIDER_ID_TYPESAFE), api_key=ts))
        return records
    if ts:
        if ts.startswith("vck_"):
            records.append(replace(jev_preset(PROVIDER_ID_VERCEL), api_key=ts))
        else:
            records.append(replace(jev_preset(PROVIDER_ID_TYPESAFE), api_key=ts))
    if gw and gw != ts:
        if not any(item.id == PROVIDER_ID_VERCEL for item in records):
            records.append(replace(jev_preset(PROVIDER_ID_VERCEL), api_key=gw))
    return records


def _raw_setting(name: str, values: Mapping[str, str] | None) -> str:
    if values is None:
        from excelmanus.settings_runtime import get_setting

        return (get_setting(name) or "").strip()
    return str(values.get(name) or "").strip()


def load_jev_providers(values: Mapping[str, str] | None = None) -> list[JevProviderRecord]:
    stored = parse_jev_providers_json(_raw_setting(JEV_PROVIDERS_SETTING, values))
    if stored:
        return stored
    return migrate_legacy_jev_providers(
        _raw_setting(TYPESAFE_KEY_SETTING, values),
        _raw_setting(GATEWAY_KEY_SETTING, values),
    )


def pick_active_jev_provider(
    records: list[JevProviderRecord],
    active_id: str | None,
) -> JevProviderRecord | None:
    wanted = (active_id or "").strip()
    if wanted:
        for item in records:
            if item.id == wanted:
                return item
    for item in records:
        if item.configured:
            return item
    return records[0] if records else None


def resolve_jev_connection(
    records: list[JevProviderRecord],
    active_id: str | None,
    *,
    typesafe_key: str | None = None,
    gateway_key: str | None = None,
    model_override: str | None = None,
) -> JevConnection:
    """Resolve one coherent Jev connection.

    A legacy key is only used with the matching protocol.  In particular, a
    missing Gateway key never silently turns a TypeSafe key into a Gateway
    request.  ``model_override`` remains available for the existing global
    model setting, while a real provider model wins when the global value is
    still the old default.
    """
    active = pick_active_jev_provider(records, active_id)
    ts = (typesafe_key or "").strip()
    gw = (gateway_key or "").strip()
    override = (model_override or "").strip()
    if active is None:
        if gw:
            return JevConnection(
                api_key=gw,
                protocol=JEV_PROTOCOL_GATEWAY,
                base_url=GATEWAY_URL,
                model=override or GATEWAY_MODEL,
                source=GATEWAY_KEY_SETTING,
            )
        if ts:
            if ts.startswith("vck_"):
                return JevConnection(
                    api_key=ts,
                    protocol=JEV_PROTOCOL_GATEWAY,
                    base_url=GATEWAY_URL,
                    model=override or GATEWAY_MODEL,
                    source=TYPESAFE_KEY_SETTING,
                )
            return JevConnection(
                api_key=ts,
                protocol=JEV_PROTOCOL_TYPESAFE,
                base_url=TYPESAFE_BASE_URL,
                model=override or TYPESAFE_DEFAULT_MODEL,
                source=TYPESAFE_KEY_SETTING,
            )
        return JevConnection(model=override or TYPESAFE_DEFAULT_MODEL)

    key = active.api_key.strip()
    source = JEV_PROVIDERS_SETTING if key else ""
    if key and key == ts and key != gw:
        source = TYPESAFE_KEY_SETTING
    elif key and key == gw:
        source = GATEWAY_KEY_SETTING
    protocol = normalize_jev_protocol(active.protocol)
    if not key:
        if protocol == JEV_PROTOCOL_GATEWAY and gw:
            key, source = gw, GATEWAY_KEY_SETTING
        elif protocol == JEV_PROTOCOL_TYPESAFE and ts:
            key, source = ts, TYPESAFE_KEY_SETTING
    provider_model = active.model.strip() or (
        GATEWAY_MODEL if protocol == JEV_PROTOCOL_GATEWAY else TYPESAFE_DEFAULT_MODEL
    )
    model = provider_model if not override or override == TYPESAFE_DEFAULT_MODEL else override
    base_url = active.base_url.strip()
    if not base_url and active.id in {PROVIDER_ID_TYPESAFE, PROVIDER_ID_VERCEL}:
        base_url = GATEWAY_URL if protocol == JEV_PROTOCOL_GATEWAY else TYPESAFE_BASE_URL
    return JevConnection(
        provider_id=active.id,
        api_key=key or None,
        protocol=protocol,
        base_url=base_url,
        model=model,
        source=source,
    )


def merge_jev_provider_updates(
    existing: list[JevProviderRecord],
    incoming: list[Mapping[str, Any]],
    *,
    preserve_missing: bool = False,
) -> list[JevProviderRecord]:
    previous = {item.id: item for item in existing}
    merged: list[JevProviderRecord] = list(existing) if preserve_missing else []
    seen: set[str] = set()
    for raw in incoming:
        parsed = record_from_mapping(raw)
        if parsed is None or parsed.id in seen:
            continue
        seen.add(parsed.id)
        key = parsed.api_key
        if bool(raw.get("clear_api_key")):
            key = ""
        elif not key or "*" in key:
            key = previous.get(parsed.id, parsed).api_key
        updated = JevProviderRecord(
            id=parsed.id,
            name=parsed.name,
            protocol=parsed.protocol,
            base_url=parsed.base_url,
            model=parsed.model,
            api_key=key,
        )
        for index, item in enumerate(merged):
            if item.id == updated.id:
                merged[index] = updated
                break
        else:
            merged.append(updated)
    return merged


def legacy_keys_from_providers(records: list[JevProviderRecord]) -> tuple[str, str]:
    typesafe = next((item.api_key for item in records if item.id == PROVIDER_ID_TYPESAFE), "")
    vercel = next((item.api_key for item in records if item.id == PROVIDER_ID_VERCEL), "")
    return typesafe, vercel
