"""激活模型：档案凭证不互相继承，运行时只保留一份快照。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from excelmanus.api_app_state import (
    apply_profile_to_config,
    ensure_active_model,
    get_config_incomplete,
    is_placeholder_model_profile,
    set_config,
    set_config_incomplete,
    set_config_store,
    _sync_config_profiles_from_db,
)
from excelmanus.api_routes_config import _resolve_model_info


def _config(**overrides):
    values = dict(
        model="snapshot-model",
        api_key="snapshot-key",
        base_url="https://snapshot.example.com/v1",
        protocol="openai",
        models=(),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _store(profiles: list[dict], active: str = ""):
    store = MagicMock()
    kv = {"active_model": active} if active else {}
    store.get.side_effect = lambda key, default="": kv.get(key, default)
    store.list_profiles.return_value = profiles
    store.get_profile.side_effect = lambda name: next(
        (p for p in profiles if p["name"] == name), None
    )
    return store


def test_apply_profile_overwrites_snapshot_including_empty_key() -> None:
    config = _config()
    store = _store([{
        "name": "alt",
        "model": "alt-model",
        "api_key": "",
        "base_url": "https://alt.example.com/v1",
        "protocol": "openai",
    }])
    set_config(config)
    set_config_store(store)
    set_config_incomplete(False)
    try:
        assert apply_profile_to_config("alt") is True
        assert config.model == "alt-model"
        assert config.api_key == ""
        assert config.base_url == "https://alt.example.com/v1"
        assert get_config_incomplete() is True
    finally:
        set_config(None)
        set_config_store(None)
        set_config_incomplete(False)


def test_apply_codex_profile_is_complete_without_api_key() -> None:
    config = _config()
    store = _store([{
        "name": "openai-codex/gpt-6-astra",
        "model": "openai-codex/gpt-6-astra",
        "api_key": "",
        "base_url": "",
        "protocol": "openai_responses",
    }])
    set_config(config)
    set_config_store(store)
    set_config_incomplete(True)
    try:
        assert apply_profile_to_config("openai-codex/gpt-6-astra") is True
        assert config.api_key == ""
        assert get_config_incomplete() is False
    finally:
        set_config(None)
        set_config_store(None)
        set_config_incomplete(False)


def test_sync_profiles_skips_placeholder_rows() -> None:
    config = _config()
    store = _store([
        {
            "name": "test-model",
            "model": "test-model",
            "api_key": "kept-key",
            "base_url": "https://example.com/v1",
            "protocol": "openai",
            "description": "",
            "thinking_mode": "auto",
            "model_family": "",
            "custom_extra_body": "",
            "custom_extra_headers": "",
        },
        {
            "name": "DeepSeek",
            "model": "deepseek-flash",
            "api_key": "sk-live",
            "base_url": "https://api.deepseek.com/v1",
            "protocol": "openai",
            "description": "",
            "thinking_mode": "auto",
            "model_family": "",
            "custom_extra_body": "",
            "custom_extra_headers": "",
        },
    ])
    set_config(config)
    set_config_store(store)
    try:
        _sync_config_profiles_from_db()
        assert [p.name for p in config.models] == ["DeepSeek"]
    finally:
        set_config(None)
        set_config_store(None)


def test_sync_profiles_does_not_inherit_snapshot_credentials() -> None:
    config = _config()
    store = _store([{
        "name": "empty-key",
        "model": "gpt-test",
        "api_key": "",
        "base_url": "",
        "protocol": "auto",
        "description": "",
        "thinking_mode": "auto",
        "model_family": "",
        "custom_extra_body": "",
        "custom_extra_headers": "",
    }])
    set_config(config)
    set_config_store(store)
    try:
        _sync_config_profiles_from_db()
        assert len(config.models) == 1
        assert config.models[0].api_key == ""
        assert config.models[0].base_url == ""
        assert config.models[0].model == "gpt-test"
    finally:
        set_config(None)
        set_config_store(None)


def test_placeholder_profile_detection() -> None:
    assert is_placeholder_model_profile("test-model", "test-model", "https://example.com/v1")
    assert is_placeholder_model_profile("DeepSeek", "deepseek-flash", "https://example.com/v1")
    assert not is_placeholder_model_profile(
        "DeepSeek", "deepseek-flash", "https://api.deepseek.com/v1",
    )


def test_load_config_uses_active_profile() -> None:
    import os
    from excelmanus.config import load_config

    store = _store([{
        "name": "DeepSeek",
        "model": "deepseek-flash",
        "api_key": "sk-live",
        "base_url": "https://api.deepseek.com/v1",
        "protocol": "openai",
    }], active="DeepSeek")
    set_config_store(store)
    try:
        cfg = load_config()
        assert cfg.api_key == "sk-live"
        assert cfg.base_url == "https://api.deepseek.com/v1"
        assert cfg.model == "deepseek-flash"
        assert cfg.protocol == "openai"
        assert os.environ.get("EXCELMANUS_API_KEY") in (None, "")
    finally:
        set_config_store(None)


def test_load_config_ignores_process_env_credentials() -> None:
    import os
    from excelmanus.config import load_config

    store = _store([{
        "name": "DeepSeek",
        "model": "deepseek-flash",
        "api_key": "sk-live",
        "base_url": "https://api.deepseek.com/v1",
        "protocol": "openai",
    }], active="DeepSeek")
    set_config_store(store)
    try:
        os.environ["EXCELMANUS_API_KEY"] = "sk-env"
        os.environ["EXCELMANUS_BASE_URL"] = "https://env.example.com/v1"
        os.environ["EXCELMANUS_MODEL"] = "env-model"
        cfg = load_config()
        assert cfg.api_key == "sk-live"
        assert cfg.model == "deepseek-flash"
    finally:
        os.environ.pop("EXCELMANUS_API_KEY", None)
        os.environ.pop("EXCELMANUS_BASE_URL", None)
        os.environ.pop("EXCELMANUS_MODEL", None)
        set_config_store(None)


def test_load_config_skips_placeholder_and_uses_first_real() -> None:
    import os
    from excelmanus.config import load_config

    store = _store([{
        "name": "test-model",
        "model": "test-model",
        "api_key": "sk-placeholder",
        "base_url": "https://example.com/v1",
        "protocol": "openai",
    }, {
        "name": "DeepSeek",
        "model": "deepseek-flash",
        "api_key": "sk-live",
        "base_url": "https://api.deepseek.com/v1",
        "protocol": "openai",
    }], active="test-model")
    set_config_store(store)
    try:
        cfg = load_config()
        assert cfg.model == "deepseek-flash"
        assert cfg.api_key == "sk-live"
        assert os.environ.get("EXCELMANUS_API_KEY") in (None, "")
    finally:
        set_config_store(None)


def test_ensure_active_model_skips_placeholder_env_migration() -> None:
    config = _config(model="test-model", api_key="kept-key", base_url="https://example.com/v1")
    store = _store([])
    store.add_profile = MagicMock()
    user = MagicMock()
    user.get_active_model.return_value = None
    set_config(config)
    set_config_store(store)
    try:
        with patch("excelmanus.api_app_state._user_config_store", return_value=user):
            ensure_active_model()
        store.add_profile.assert_not_called()
        user.set_active_model.assert_not_called()
        assert config.model == "test-model"
    finally:
        set_config(None)
        set_config_store(None)


def test_ensure_active_model_applies_named_profile() -> None:
    config = _config(model="test-model", api_key="kept-key", base_url="https://example.com/v1")
    store = _store([{
        "name": "DeepSeek",
        "model": "deepseek-flash",
        "api_key": "sk-live",
        "base_url": "https://api.deepseek.com/v1",
        "protocol": "openai",
    }])
    user = MagicMock()
    user.get_active_model.return_value = "DeepSeek"
    set_config(config)
    set_config_store(store)
    try:
        with patch("excelmanus.api_app_state._user_config_store", return_value=user):
            ensure_active_model()
        assert config.model == "deepseek-flash"
        assert config.api_key == "sk-live"
        assert config.base_url == "https://api.deepseek.com/v1"
    finally:
        set_config(None)
        set_config_store(None)


def test_ensure_active_model_purges_placeholder_profile() -> None:
    profiles = [{
        "name": "test-model",
        "model": "test-model",
        "api_key": "kept-key",
        "base_url": "https://example.com/v1",
        "protocol": "openai",
        "description": "",
        "thinking_mode": "auto",
        "model_family": "",
        "custom_extra_body": "",
        "custom_extra_headers": "",
    }, {
        "name": "DeepSeek",
        "model": "deepseek-flash",
        "api_key": "sk-live",
        "base_url": "https://api.deepseek.com/v1",
        "protocol": "openai",
        "description": "",
        "thinking_mode": "auto",
        "model_family": "",
        "custom_extra_body": "",
        "custom_extra_headers": "",
    }]
    store = MagicMock()
    store.list_profiles.side_effect = lambda: list(profiles)
    store.get_profile.side_effect = lambda name: next(
        (p for p in profiles if p["name"] == name), None
    )

    def _delete(name: str) -> bool:
        profiles[:] = [p for p in profiles if p["name"] != name]
        return True

    store.delete_profile.side_effect = _delete
    user = MagicMock()
    user.get_active_model.return_value = "DeepSeek"
    config = _config(model="test-model", api_key="kept-key", base_url="https://example.com/v1")
    set_config(config)
    set_config_store(store)
    try:
        with patch("excelmanus.api_app_state._user_config_store", return_value=user):
            ensure_active_model()
        store.delete_profile.assert_called_once_with("test-model")
        assert config.model == "deepseek-flash"
        assert [p["name"] for p in profiles] == ["DeepSeek"]
    finally:
        set_config(None)
        set_config_store(None)


def test_resolve_model_info_uses_profile_own_credentials() -> None:
    config = _config()
    store = _store([{
        "name": "alt",
        "model": "alt-model",
        "api_key": "",
        "base_url": "https://alt.example.com/v1",
        "protocol": "openai",
    }])
    set_config(config)
    set_config_store(store)
    try:
        model, base_url, api_key, protocol = _resolve_model_info("alt", None, None)
        assert model == "alt-model"
        assert base_url == "https://alt.example.com/v1"
        assert api_key == ""
        assert protocol == "openai"
    finally:
        set_config(None)
        set_config_store(None)


def test_resolve_model_info_does_not_borrow_key_for_foreign_base_url() -> None:
    config = _config()
    store = _store([])
    set_config(config)
    set_config_store(store)
    try:
        model, base_url, api_key, protocol = _resolve_model_info(
            None, "acme/model-2", "https://api.example.com/v1",
        )
        assert model == "acme/model-2"
        assert base_url == "https://api.example.com/v1"
        assert api_key == ""
        assert protocol == "openai"
    finally:
        set_config(None)
        set_config_store(None)


def test_masked_api_key_is_not_usable() -> None:
    from excelmanus.api_routes_config import _is_masked_api_key, _mask_key, _usable_api_key

    raw = "sk-test-secret-aaaaaaaa"
    masked = _mask_key(raw)
    assert _is_masked_api_key(masked)
    assert _usable_api_key(masked) == ""
    assert _usable_api_key(raw) == raw
    assert _usable_api_key("****") == ""
    assert _usable_api_key("  ") == ""
