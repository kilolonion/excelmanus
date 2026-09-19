from excelmanus.system_one.providers import (
    PROVIDER_ID_TYPESAFE,
    PROVIDER_ID_VERCEL,
    jev_preset,
    load_jev_providers,
    merge_jev_provider_updates,
    migrate_legacy_jev_providers,
    parse_jev_providers_json,
    pick_active_jev_provider,
    public_jev_provider,
    serialize_jev_providers,
)


def test_migrate_mirrored_gateway_key() -> None:
    key = "vck_" + ("x" * 8)
    records = migrate_legacy_jev_providers(key, key)
    assert [item.id for item in records] == [PROVIDER_ID_VERCEL]
    assert records[0].api_key == key
    assert records[0].protocol == "gateway"


def test_migrate_splits_typesafe_and_vercel() -> None:
    records = migrate_legacy_jev_providers("ts_direct", "vck_gateway")
    assert [item.id for item in records] == [PROVIDER_ID_TYPESAFE, PROVIDER_ID_VERCEL]
    assert records[0].api_key == "ts_direct"
    assert records[1].api_key == "vck_gateway"


def test_merge_keeps_existing_key_when_masked() -> None:
    existing = migrate_legacy_jev_providers("ts_direct", None)
    merged = merge_jev_provider_updates(
        existing,
        [{"id": "typesafe", "name": "TypeSafe", "protocol": "typesafe", "api_key": "****abcd"}],
    )
    assert merged[0].api_key == "ts_direct"


def test_merge_can_add_custom_and_drop_builtin() -> None:
    existing = migrate_legacy_jev_providers("ts_direct", "vck_g")
    merged = merge_jev_provider_updates(
        existing,
        [
            {
                "id": "custom-1",
                "name": "自建网关",
                "protocol": "gateway",
                "base_url": "https://example.test/evaluate",
                "model": "typesafe-ai/jev",
                "api_key": "vck_custom",
            }
        ],
    )
    assert [item.id for item in merged] == ["custom-1"]
    assert merged[0].name == "自建网关"


def test_public_view_never_includes_key() -> None:
    record = jev_preset(PROVIDER_ID_TYPESAFE)
    from dataclasses import replace

    public = public_jev_provider(replace(record, api_key="ts_secret_key"))
    assert public["configured"] is True
    assert public["last4"] == "ts_secret_key"[-4:]
    assert "api_key" not in public
    dumped = serialize_jev_providers([replace(record, api_key="ts_secret_key")])
    parsed = parse_jev_providers_json(dumped)
    assert parsed[0].api_key == "ts_secret_key"


def test_pick_active_prefers_requested_then_configured() -> None:
    records = migrate_legacy_jev_providers("ts_direct", "vck_g")
    assert pick_active_jev_provider(records, "vercel").id == "vercel"
    assert pick_active_jev_provider(records, "missing").id == "typesafe"


def test_load_from_values_uses_json_over_legacy() -> None:
    dumped = serialize_jev_providers(
        merge_jev_provider_updates(
            [],
            [{
                "id": "custom-2",
                "name": "Custom",
                "protocol": "typesafe",
                "base_url": "https://api.example",
                "model": "jev-1.13.0",
                "api_key": "abc",
            }],
        )
    )
    loaded = load_jev_providers({
        "EXCELMANUS_JEV_PROVIDERS": dumped,
        "EXCELMANUS_TYPESAFE_API_KEY": "legacy",
    })
    assert [item.id for item in loaded] == ["custom-2"]


def test_patch_update_preserves_omitted_providers_but_replace_drops_them() -> None:
    existing = migrate_legacy_jev_providers("ts_direct", "vck_gateway")
    incoming = [{"id": "custom", "name": "Custom", "protocol": "gateway", "api_key": "vck_custom"}]
    patched = merge_jev_provider_updates(existing, incoming, preserve_missing=True)
    assert [item.id for item in patched] == ["typesafe", "vercel", "custom"]
    replaced = merge_jev_provider_updates(existing, incoming, preserve_missing=False)
    assert [item.id for item in replaced] == ["custom"]


def test_explicit_clear_api_key_does_not_reuse_previous_secret() -> None:
    existing = migrate_legacy_jev_providers("ts_direct", None)
    merged = merge_jev_provider_updates(
        existing,
        [{"id": "typesafe", "name": "TypeSafe", "protocol": "typesafe", "clear_api_key": True}],
    )
    assert merged[0].api_key == ""
